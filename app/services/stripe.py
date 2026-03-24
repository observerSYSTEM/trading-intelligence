from __future__ import annotations

import threading

import stripe

from app.core.config import settings

_validated_price_ids: set[str] = set()
_price_validation_lock = threading.Lock()


def _stripe_key_mode(secret_key: str | None) -> str:
    key = (secret_key or "").strip()
    if key.startswith("sk_live"):
        return "live"
    if key.startswith("sk_test"):
        return "test"
    return "unknown"


def _price_map() -> dict[str, str]:
    return {
        "basic": settings.stripe_price_basic,
        "pro": settings.stripe_price_pro,
        "elite": settings.stripe_price_elite,
    }


def _ensure_price_id_usable(price_id: str) -> None:
    cleaned = price_id.strip()
    if not cleaned:
        raise ValueError("Stripe price id is empty")
    if cleaned in _validated_price_ids:
        return

    stripe.api_key = settings.STRIPE_SECRET_KEY
    mode = _stripe_key_mode(settings.STRIPE_SECRET_KEY)
    if mode == "unknown":
        raise ValueError("STRIPE_SECRET_KEY must start with sk_test or sk_live")

    with _price_validation_lock:
        if cleaned in _validated_price_ids:
            return
        try:
            stripe.Price.retrieve(cleaned)
        except stripe.error.InvalidRequestError as exc:
            raise ValueError(
                f"Stripe price '{cleaned}' not found for current {mode} key. "
                "Check test/live key and price configuration."
            ) from exc
        _validated_price_ids.add(cleaned)


def validate_price_catalog() -> dict[str, str]:
    result: dict[str, str] = {}
    for plan, price_id in _price_map().items():
        if not price_id:
            result[plan] = "missing"
            continue
        try:
            _ensure_price_id_usable(price_id)
            result[plan] = "ok"
        except Exception as exc:
            result[plan] = f"error:{exc}"
    return result


def create_checkout_session(
    *,
    plan: str,
    user_id: str | None,
    customer_id: str | None,
    customer_email: str | None,
    success_url: str,
    cancel_url: str,
):
    prices = _price_map()
    if plan not in prices:
        raise ValueError("Invalid plan")
    price_id = prices.get(plan)
    if not price_id:
        raise ValueError(f"Missing Stripe price configuration for plan={plan}")

    _ensure_price_id_usable(price_id)
    stripe.api_key = settings.STRIPE_SECRET_KEY
    metadata: dict[str, str] = {"plan": plan}
    if user_id:
        metadata["user_id"] = user_id

    payload: dict[str, object] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": success_url,
        "cancel_url": cancel_url,
        "metadata": metadata,
    }
    if customer_id:
        payload["customer"] = customer_id
    elif customer_email:
        payload["customer_email"] = customer_email

    session = stripe.checkout.Session.create(**payload)
    return session.url
