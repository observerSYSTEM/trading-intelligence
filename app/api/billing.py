# app/api/billing.py

import uuid
from datetime import datetime, timezone
import logging

import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_optional
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import (
    NotificationRoute,
    StripeWebhookEvent,
    StripeWebhookIdempotency,
    Subscription,
    User,
    UserSignalPref,
)
from app.db.session import get_db
from app.schemas.billing import CheckoutActivationIn, CheckoutActivationOut, CheckoutSessionIn
from app.services.account_activation import create_activation_token
from app.services.audit import log_audit
from app.services.stripe import create_checkout_session
from app.services.telegram_service import send_message as send_telegram_message

router = APIRouter(prefix="/billing", tags=["billing"])
logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _plan_from_price_id(price_id: str | None) -> str | None:
    clean_price_id = (price_id or "").strip()
    if not clean_price_id:
        return None
    mapping = {
        settings.stripe_price_basic: "basic",
        settings.stripe_price_pro: "pro",
        settings.stripe_price_elite: "elite",
    }
    return mapping.get(clean_price_id)


def _extract_checkout_email(session_obj: dict) -> str:
    details = session_obj.get("customer_details") or {}
    return _normalize_email(details.get("email") or session_obj.get("customer_email"))


def _extract_checkout_name(session_obj: dict) -> str:
    details = session_obj.get("customer_details") or {}
    return " ".join(str(details.get("name") or "").strip().split())


def _resolve_checkout_plan(session_obj: dict) -> str | None:
    session_id = str(session_obj.get("id") or "").strip()
    price_id: str | None = None
    if session_id:
        try:
            line_items = stripe.checkout.Session.list_line_items(session_id, limit=5)
            entries = line_items.get("data") or []
            if entries and isinstance(entries[0], dict):
                price_obj = entries[0].get("price")
                if isinstance(price_obj, dict):
                    price_id = str(price_obj.get("id") or "").strip()
                elif isinstance(price_obj, str):
                    price_id = price_obj.strip()
        except Exception:
            logger.exception("Failed to read Stripe line items for checkout session %s", session_id)
    plan = _plan_from_price_id(price_id)
    if plan:
        return plan

    metadata = session_obj.get("metadata") or {}
    fallback = str(metadata.get("plan") or "").strip().lower()
    if fallback in {"basic", "pro", "elite"}:
        return fallback
    return None


def _find_or_create_user_by_email(
    db: Session,
    *,
    email: str,
    full_name: str | None,
) -> User | None:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return None

    user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
    if user:
        if (not (user.full_name or "").strip()) and full_name:
            user.full_name = full_name
        return user

    fallback_name = full_name or normalized_email.split("@")[0].replace(".", " ").title()
    user = User(
        full_name=fallback_name,
        email=normalized_email,
        password_hash=None,
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()

    db.add(
        UserSignalPref(
            user_id=user.id,
            symbols_json=["XAUUSD"],
            telegram_enabled=False,
            telegram_chat_id=None,
        )
    )
    db.flush()
    return user


def _find_subscription(
    db: Session,
    *,
    stripe_subscription_id: str | None,
    stripe_customer_id: str | None,
) -> Subscription | None:
    if stripe_subscription_id:
        row = (
            db.query(Subscription)
            .filter(Subscription.stripe_subscription_id == stripe_subscription_id)
            .first()
        )
        if row:
            return row
    if stripe_customer_id:
        return (
            db.query(Subscription)
            .filter(Subscription.stripe_customer_id == stripe_customer_id)
            .first()
        )
    return None


def _extract_invoice_period_end(invoice_obj: dict) -> int | None:
    period_end = invoice_obj.get("period_end")
    if isinstance(period_end, int):
        return period_end
    lines = invoice_obj.get("lines")
    if not isinstance(lines, dict):
        return None
    data = lines.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, dict):
        return None
    period = first.get("period")
    if not isinstance(period, dict):
        return None
    end = period.get("end")
    if isinstance(end, int):
        return end
    return None


def _notify_billing_telegram(
    db: Session,
    *,
    request: Request,
    subscription: Subscription,
    title: str,
    body: str,
    action_success: str,
) -> None:
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == subscription.user_id).first()
    pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == subscription.user_id).first()
    pref_enabled = bool(pref.telegram_enabled) if pref else False
    pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
    route_enabled = bool(route.telegram_enabled) if route else False
    route_chat = (route.telegram_chat_id or "").strip() if route else ""
    enabled = pref_enabled or route_enabled
    chat_id = pref_chat or route_chat
    if not enabled or not chat_id:
        return

    text = f"{title}\n{body}\n\nOpen dashboard \u2192 Manage Billing"
    try:
        send_telegram_message(chat_id, text)
        log_audit(
            db,
            action=action_success,
            user_id=subscription.user_id,
            request=request,
            meta={"plan": subscription.plan, "status": subscription.status},
        )
    except Exception as exc:
        log_audit(
            db,
            action=f"{action_success}.failed",
            user_id=subscription.user_id,
            request=request,
            meta={"error": str(exc), "plan": subscription.plan, "status": subscription.status},
        )


def _billing_disabled_reason() -> str | None:
    missing: list[str] = []
    if not settings.STRIPE_SECRET_KEY.strip():
        missing.append("STRIPE_SECRET_KEY")
    if not settings.stripe_price_basic:
        missing.append("STRIPE_PRICE_ID_BASIC/STRIPE_PRICE_BASIC")
    if not settings.stripe_price_pro:
        missing.append("STRIPE_PRICE_ID_PRO/STRIPE_PRICE_PRO")
    if not settings.stripe_price_elite:
        missing.append("STRIPE_PRICE_ID_ELITE/STRIPE_PRICE_ELITE")
    if missing:
        reason = f"Billing disabled: missing Stripe configuration ({', '.join(missing)})"
        logger.error(reason)
        return reason
    return None


@router.post("/checkout-session")
def checkout_session(
    payload: CheckoutSessionIn,
    request: Request,
    user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "billing_checkout",
        (
            RateLimitRule(limit=30, window_seconds=60),
            RateLimitRule(limit=300, window_seconds=3600),
        ),
    ),
):
    disabled_reason = _billing_disabled_reason()
    if disabled_reason:
        raise HTTPException(status_code=503, detail=disabled_reason)

    sub: Subscription | None = None
    customer_id: str | None = None
    user_id: str | None = None
    customer_email: str | None = _normalize_email(str(payload.email)) if payload.email else None

    if user is not None:
        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        if not sub:
            sub = Subscription(user_id=user.id, plan="basic", status="inactive")
            db.add(sub)
            db.commit()
            db.refresh(sub)
        user_id = str(user.id)
        customer_id = sub.stripe_customer_id
        if user.email:
            customer_email = _normalize_email(user.email)

    try:
        url = create_checkout_session(
            plan=payload.plan,
            user_id=user_id,
            customer_id=customer_id,
            customer_email=customer_email,
            success_url=f"{settings.FRONTEND_URL}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{settings.FRONTEND_URL}/billing/cancelled",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_audit(
        db,
        action="billing.checkout_session.created",
        user_id=user.id if user else None,
        request=request,
        meta={"plan": payload.plan, "guest_checkout": user is None},
    )
    db.commit()
    return {"url": url}


@router.post("/portal")
def billing_portal(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "billing_portal",
        (
            RateLimitRule(limit=30, window_seconds=60),
            RateLimitRule(limit=300, window_seconds=3600),
        ),
    ),
):
    disabled_reason = _billing_disabled_reason()
    if disabled_reason:
        raise HTTPException(status_code=503, detail=disabled_reason)

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub or not sub.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No Stripe customer found for this user")

    session = stripe.billing_portal.Session.create(
        customer=sub.stripe_customer_id,
        return_url=f"{settings.FRONTEND_URL}/dashboard",
    )
    log_audit(
        db,
        action="billing.portal_session.created",
        user_id=user.id,
        request=request,
        meta={"has_customer_id": True},
    )
    db.commit()
    return {"url": session.url}


@router.get("/cancelled", include_in_schema=False)
def billing_cancelled_redirect():
    return RedirectResponse(url=f"{settings.FRONTEND_URL}/billing/cancelled", status_code=307)


@router.get("/success", include_in_schema=False)
def billing_success_redirect(session_id: str | None = None):
    target = f"{settings.FRONTEND_URL}/billing/success"
    if session_id:
        target = f"{target}?session_id={session_id}"
    return RedirectResponse(url=target, status_code=307)


@router.post("/checkout-activation", response_model=CheckoutActivationOut)
def checkout_activation(
    payload: CheckoutActivationIn,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "billing_checkout_activation",
        (
            RateLimitRule(limit=60, window_seconds=60),
            RateLimitRule(limit=600, window_seconds=3600),
        ),
    ),
):
    disabled_reason = _billing_disabled_reason()
    if disabled_reason:
        raise HTTPException(status_code=503, detail=disabled_reason)

    session_id = payload.session_id.strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="Missing session_id")

    try:
        checkout_session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.InvalidRequestError as exc:
        raise HTTPException(status_code=404, detail="Stripe session not found") from exc

    session_status = str(checkout_session.get("status") or "").strip().lower()
    payment_status = str(checkout_session.get("payment_status") or "").strip().lower()
    if session_status != "complete":
        return CheckoutActivationOut(
            ready=False,
            requires_password_setup=False,
            message="Checkout is not completed yet.",
        )
    if payment_status and payment_status not in {"paid", "no_payment_required"}:
        return CheckoutActivationOut(
            ready=False,
            requires_password_setup=False,
            message="Payment confirmation is still processing.",
        )

    email = _extract_checkout_email(checkout_session)
    if not email:
        return CheckoutActivationOut(
            ready=False,
            requires_password_setup=False,
            message="Checkout is complete but email is not available yet.",
        )

    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user:
        return CheckoutActivationOut(
            ready=False,
            requires_password_setup=False,
            message="Account setup is processing. Please refresh in a few seconds.",
            email=email,
        )

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub or (sub.status or "").lower() not in {"active", "trialing"}:
        return CheckoutActivationOut(
            ready=False,
            requires_password_setup=False,
            message="Subscription setup is still processing. Please refresh shortly.",
            email=email,
        )

    if (user.password_hash or "").strip():
        return CheckoutActivationOut(
            ready=True,
            requires_password_setup=False,
            message="Account already has a password. Please log in.",
            email=email,
        )

    token = create_activation_token(
        db,
        user_id=user.id,
        ttl_minutes=settings.ACCOUNT_ACTIVATION_TOKEN_TTL_MINUTES,
    )
    db.commit()
    return CheckoutActivationOut(
        ready=True,
        requires_password_setup=True,
        message="Account setup is ready.",
        email=email,
        activation_token=token,
        expires_in_seconds=max(int(settings.ACCOUNT_ACTIVATION_TOKEN_TTL_MINUTES), 5) * 60,
    )


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "billing_webhook",
        (
            RateLimitRule(limit=240, window_seconds=60),
            RateLimitRule(limit=5000, window_seconds=3600),
        ),
    ),
):
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature")

    if not settings.STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Billing disabled: STRIPE_WEBHOOK_SECRET is not configured")
    if not sig:
        raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=sig,
            secret=settings.STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid webhook payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    event_id = event.get("id")
    event_type = event.get("type")
    if not event_id or not event_type:
        raise HTTPException(status_code=400, detail="Invalid event metadata")

    idempotency_marker = StripeWebhookIdempotency(event_id=event_id)
    db.add(idempotency_marker)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        log_audit(
            db,
            action="billing.webhook.duplicate",
            request=request,
            meta={"event_id": event_id, "event_type": event_type},
        )
        db.commit()
        return {"ok": True, "duplicate": True}

    marker = StripeWebhookEvent(event_id=event_id, event_type=event_type, processed=False)
    db.add(marker)
    db.flush()

    allowed = {
        "checkout.session.completed",
        "customer.subscription.updated",
        "invoice.payment_failed",
        "invoice.payment_succeeded",
        "customer.subscription.deleted",
    }
    if event_type not in allowed:
        marker.processed = True
        marker.processed_at = datetime.now(timezone.utc)
        log_audit(
            db,
            action="billing.webhook.ignored",
            request=request,
            meta={"event_id": event_id, "event_type": event_type},
        )
        db.commit()
        return {"ok": True, "ignored": True}

    obj = event["data"]["object"]

    if event_type == "checkout.session.completed":
        metadata = obj.get("metadata") or {}
        user_id_str = str(metadata.get("user_id") or "").strip()
        customer_id = str(obj.get("customer") or "").strip() or None
        stripe_subscription_id = str(obj.get("subscription") or "").strip() or None
        plan = _resolve_checkout_plan(obj) or "basic"

        checkout_email = _extract_checkout_email(obj)
        checkout_name = _extract_checkout_name(obj)

        user: User | None = None
        if user_id_str:
            try:
                user_uuid = uuid.UUID(user_id_str)
            except Exception:
                user_uuid = None
            if user_uuid is not None:
                user = db.query(User).filter(User.id == user_uuid).first()

        if user is None and checkout_email:
            user = _find_or_create_user_by_email(
                db,
                email=checkout_email,
                full_name=checkout_name or None,
            )

        if user is None:
            marker.processed = True
            marker.processed_at = datetime.now(timezone.utc)
            log_audit(
                db,
                action="billing.webhook.checkout.missing_user",
                request=request,
                meta={"event_id": event_id, "has_email": bool(checkout_email)},
            )
            db.commit()
            return {"ok": True, "ignored": True}

        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        if not sub:
            sub = Subscription(user_id=user.id, plan="basic", status="inactive")
            db.add(sub)

        sub.plan = plan
        sub.status = "active"
        sub.stripe_customer_id = customer_id
        sub.stripe_subscription_id = stripe_subscription_id

        if stripe_subscription_id:
            try:
                stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
                stripe_sub_status = str(stripe_sub.get("status") or "").strip().lower()
                if stripe_sub_status:
                    sub.status = stripe_sub_status
                period_end = stripe_sub.get("current_period_end")
                if isinstance(period_end, int):
                    sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
            except Exception:
                logger.exception(
                    "Unable to hydrate subscription period from Stripe subscription %s",
                    stripe_subscription_id,
                )

        if not (user.password_hash or "").strip():
            create_activation_token(
                db,
                user_id=user.id,
                ttl_minutes=settings.ACCOUNT_ACTIVATION_TOKEN_TTL_MINUTES,
            )

    elif event_type == "customer.subscription.updated":
        stripe_subscription_id = obj.get("id")
        customer_id = obj.get("customer")
        status = obj.get("status")
        current_period_end = obj.get("current_period_end")

        sub = _find_subscription(
            db,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=customer_id,
        )

        if sub and status:
            sub.status = status
            if customer_id and not sub.stripe_customer_id:
                sub.stripe_customer_id = customer_id
            if stripe_subscription_id and not sub.stripe_subscription_id:
                sub.stripe_subscription_id = stripe_subscription_id
            if current_period_end:
                sub.current_period_end = datetime.fromtimestamp(current_period_end, tz=timezone.utc)

    elif event_type == "invoice.payment_failed":
        stripe_subscription_id = obj.get("subscription")
        customer_id = obj.get("customer")
        period_end = _extract_invoice_period_end(obj)
        sub = _find_subscription(
            db,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=customer_id,
        )
        if sub:
            sub.status = "past_due"
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
            if customer_id and not sub.stripe_customer_id:
                sub.stripe_customer_id = customer_id
            if stripe_subscription_id and not sub.stripe_subscription_id:
                sub.stripe_subscription_id = stripe_subscription_id
            due_text = (
                f"Current period end: {_as_utc(sub.current_period_end).isoformat()}"
                if _as_utc(sub.current_period_end)
                else "Current period end is pending update."
            )
            _notify_billing_telegram(
                db,
                request=request,
                subscription=sub,
                title="Payment issue detected",
                body=(
                    "Your subscription payment failed and your status is now past_due.\n"
                    f"{due_text}\n"
                    "Please update your payment method to avoid interruption."
                ),
                action_success="billing.payment_failed.telegram.sent",
            )

    elif event_type == "invoice.payment_succeeded":
        stripe_subscription_id = obj.get("subscription")
        customer_id = obj.get("customer")
        period_end = _extract_invoice_period_end(obj)
        sub = _find_subscription(
            db,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=customer_id,
        )
        if sub:
            sub.status = "active"
            if period_end:
                sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)
            if customer_id and not sub.stripe_customer_id:
                sub.stripe_customer_id = customer_id
            if stripe_subscription_id and not sub.stripe_subscription_id:
                sub.stripe_subscription_id = stripe_subscription_id
            renew_text = (
                f"Next renewal: {_as_utc(sub.current_period_end).isoformat()}"
                if _as_utc(sub.current_period_end)
                else "Next renewal date is pending update."
            )
            _notify_billing_telegram(
                db,
                request=request,
                subscription=sub,
                title="Payment received",
                body=(
                    "Your subscription payment succeeded and your status is active.\n"
                    f"{renew_text}"
                ),
                action_success="billing.payment_succeeded.telegram.sent",
            )

    elif event_type == "customer.subscription.deleted":
        stripe_subscription_id = obj.get("id")
        customer_id = obj.get("customer")

        sub = _find_subscription(
            db,
            stripe_subscription_id=stripe_subscription_id,
            stripe_customer_id=customer_id,
        )

        if sub:
            sub.plan = "basic"
            sub.status = "canceled"
            sub.stripe_subscription_id = None

    marker.processed = True
    marker.processed_at = datetime.now(timezone.utc)
    log_audit(
        db,
        action="billing.webhook.processed",
        request=request,
        meta={"event_id": event_id, "event_type": event_type},
    )
    db.commit()
    return {"ok": True, "duplicate": False}
