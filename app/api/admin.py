from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import DeliveryLog, GoldRegimeDaily, NotificationRoute, SignalEvent, Subscription, User, WeeklyRangeSnapshot
from app.db.session import get_db
from app.services.oracle_snapshot import compute_dual_timeframe_snapshot, regime_from_direction
from app.services.symbol_preferences import get_user_enabled_symbols
from app.services.telegram import send_telegram_message
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage

router = APIRouter(prefix="/admin", tags=["admin"])

ACTIVE_SUB_STATUSES = {"active", "trialing"}
TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


class OracleRunIn(BaseModel):
    symbol: str = "XAUUSD"


class AdminSignalSendIn(BaseModel):
    tier_min: Literal["basic", "pro", "elite"] = "basic"
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    title: str = Field(..., min_length=1, max_length=160)
    message: str = Field(..., min_length=1, max_length=2000)


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if getattr(user, "role", "user") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_plan(plan: str | None) -> str:
    return (plan or "basic").lower()


def _signal_text(*, title: str, message: str, symbol: str) -> str:
    return f"<b>{title}</b>\n<b>Symbol:</b> {symbol}\n{message}"


def _upsert_snapshot(db: Session, result: dict) -> tuple[GoldRegimeDaily, bool]:
    snapshot = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == result["symbol"], GoldRegimeDaily.as_of_utc == result["as_of"])
        .first()
    )
    created = snapshot is None
    if snapshot is None:
        snapshot = GoldRegimeDaily(symbol=result["symbol"], as_of_utc=result["as_of"])
        db.add(snapshot)

    snapshot.regime = regime_from_direction(result["final_allowed_basic"])
    snapshot.allowed_direction = result["final_allowed_basic"]
    snapshot.final_allowed_basic = result["final_allowed_basic"]
    snapshot.final_allowed_elite = result["final_allowed_elite"]
    snapshot.daily_bias = result["daily_bias"]
    snapshot.confirm_ok = result["confirm_ok"]
    snapshot.confidence = result["confidence"]
    snapshot.notes = result["reason_basic"]
    snapshot.public_factors_json = {
        "timeframes": result["timeframes"],
        "fast_bias": result["fast_bias"],
        "confirm_tf": result["confirm_tf"],
        "confirm_ok": result["confirm_ok"],
        "final_allowed": result["final_allowed_basic"],
        "final_allowed_basic": result["final_allowed_basic"],
        "final_allowed_elite": result["final_allowed_elite"],
        "daily_bias": result["daily_bias"],
        "daily_alignment": result["daily_alignment"],
        "volume_ok": result["volume_ok"],
        "volume_state": result["volume_state"],
        "risk_gate_pass": result["risk_gate_pass"],
        "news_gate_pass": result["news_gate_pass"],
        "atr_h1": result["atr_h1"],
        "adr_d1": result["adr_d1"],
        "next_liquidity_magnet": result["next_liquidity_magnet"],
        "zone_to_zone_target": result["zone_to_zone_target"],
        "public_tier": result["public_tier"],
        "reason_basic": result["reason_basic"],
        "targets_json": result["targets_json"],
        "headline": f"Bias: {result['final_allowed_basic']}",
        "bias_m1": result["fast_bias"],
        "confirm_h1": result["confirm_ok"],
        "risk_banner": result.get("risk_banner", {}),
        "weekly_range": result.get("weekly_range", {}),
    }
    snapshot.internal_factors_json = result["internal"]
    return snapshot, created


def _upsert_weekly_range_from_result(db: Session, result: dict) -> None:
    weekly = result.get("weekly_range")
    if not isinstance(weekly, dict):
        return
    symbol = str(result.get("symbol", weekly.get("symbol", "XAUUSD"))).strip().upper()
    week_key = weekly.get("week_key")
    week_start_uk = weekly.get("week_start_uk")
    high = weekly.get("high")
    low = weekly.get("low")
    mid = weekly.get("mid")
    as_of_utc = weekly.get("as_of_utc") or result.get("as_of")
    if not (week_key and week_start_uk and high is not None and low is not None and mid is not None and as_of_utc):
        return

    row = (
        db.query(WeeklyRangeSnapshot)
        .filter(WeeklyRangeSnapshot.symbol == symbol, WeeklyRangeSnapshot.week_key == str(week_key))
        .first()
    )
    if not row:
        row = WeeklyRangeSnapshot(symbol=symbol, week_key=str(week_key))
        db.add(row)

    if isinstance(week_start_uk, datetime):
        week_start_date = week_start_uk.date()
    else:
        week_start_date = datetime.fromisoformat(str(week_start_uk)).date()

    if isinstance(as_of_utc, datetime):
        as_of_dt = as_of_utc
    else:
        as_of_text = str(as_of_utc).replace("Z", "+00:00")
        as_of_dt = datetime.fromisoformat(as_of_text)

    row.week_start_uk = week_start_date
    row.high = float(high)
    row.low = float(low)
    row.mid = float(mid)
    row.range_ready = bool(weekly.get("range_ready"))
    row.as_of_utc = _as_utc(as_of_dt)
    row.meta_json = weekly.get("meta_json") if isinstance(weekly.get("meta_json"), dict) else {}


def _snapshot_response(snapshot: GoldRegimeDaily, result: dict, created: bool) -> dict:
    as_of_iso = _as_utc(snapshot.as_of_utc).isoformat()
    return {
        "ok": True,
        "created": created,
        "symbol": snapshot.symbol,
        "title": f"{snapshot.symbol} Daily Bias Snapshot",
        "fast_bias": result["fast_bias"],
        "confirm_tf": result["confirm_tf"],
        "confirm_ok": result["confirm_ok"],
        "daily_bias": result["daily_bias"],
        "daily_alignment": result["daily_alignment"],
        "volume_ok": result["volume_ok"],
        "volume_state": result["volume_state"],
        "risk_gate_pass": result["risk_gate_pass"],
        "news_gate_pass": result["news_gate_pass"],
        "atr_h1": result["atr_h1"],
        "adr_d1": result["adr_d1"],
        "final_allowed": result["final_allowed_basic"],
        "final_allowed_basic": result["final_allowed_basic"],
        "final_allowed_elite": result["final_allowed_elite"],
        "direction": result["final_allowed_basic"],
        "message": snapshot.notes,
        "confidence": snapshot.confidence,
        "reason_basic": result["reason_basic"],
        "liquidity_magnet": result["next_liquidity_magnet"],
        "zone_to_zone_target": result["zone_to_zone_target"],
        "targets_json": result["targets_json"],
        "computed_at": as_of_iso,
        "as_of": as_of_iso,
        "timeframes": result["timeframes"],
        "timeframe": result["timeframes"]["signal"],
        "bias_m1": result["fast_bias"],
        "confirm_h1": result["confirm_ok"],
        "public_tier": result["public_tier"],
        "risk_banner": result.get("risk_banner", {}),
        "weekly_range": result.get("weekly_range", {}),
    }


def _dispatch_to_telegram(
    db: Session,
    *,
    run_id,
    symbol: str,
    tier_min: str,
    title: str,
    message: str,
    source: str,
    plan_exact: str | None = None,
    context_extra: dict | None = None,
) -> dict[str, int]:
    min_rank = TIER_ORDER[tier_min]
    text = _signal_text(title=title, message=message, symbol=symbol)

    recipients = (
        db.query(User, NotificationRoute, Subscription)
        .join(NotificationRoute, NotificationRoute.user_id == User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .filter(NotificationRoute.telegram_enabled.is_(True))
        .filter(NotificationRoute.telegram_chat_id.isnot(None))
        .filter(Subscription.status.in_(ACTIVE_SUB_STATUSES))
        .all()
    )

    sent = 0
    failed = 0
    skipped_tier = 0
    skipped_quota = 0

    for user, route, sub in recipients:
        plan = _normalize_plan(sub.plan)
        if plan_exact:
            if plan != plan_exact:
                skipped_tier += 1
                continue
        elif TIER_ORDER.get(plan, 0) < min_rank:
            skipped_tier += 1
            continue

        selected = get_user_enabled_symbols(db, user.id, plan)
        if symbol not in selected:
            skipped_tier += 1
            continue

        usage_before = get_usage(db, user.id)
        limit = usage_before.get("limit")
        used = usage_before.get("used", 0)
        context_json = {
            "title": title,
            "tier_min": tier_min,
            "symbol": symbol,
            "used_before": used,
            "limit": limit,
        }
        if context_extra:
            context_json.update(context_extra)

        if limit is not None and int(usage_before.get("remaining") or 0) <= 0:
            skipped_quota += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=symbol,
                    source=source,
                    tier=plan,
                    subscription_status=sub.status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail="usage_limit_exceeded",
                    context_json=context_json,
                )
            )
            continue

        send_status = "SENT"
        detail = None
        try:
            send_telegram_message(route.telegram_chat_id, text)
            usage_after = consume_usage(
                db,
                user.id,
                n=1,
                reason=source,
                symbol=symbol,
                signal_id=f"{source}:{run_id}",
                meta={"title": title, "tier_min": tier_min},
            )
            sent += 1
            db.add(
                SignalEvent(
                    user_id=user.id,
                    symbol=symbol,
                    status="ALLOWED",
                    public_reason_json={
                        "title": title,
                        "symbol": symbol,
                        "tier_min": tier_min,
                    },
                    internal_reason_json={
                        "source": source,
                        "plan": plan,
                        "used_before": used,
                        "used_after": usage_after.get("used"),
                        "context": context_extra or {},
                    },
                )
            )
            context_json["usage"] = usage_after
        except UsageLimitExceeded as exc:
            send_status = "SKIPPED"
            detail = "usage_limit_exceeded_post_send"
            skipped_quota += 1
            db.add(
                SignalEvent(
                    user_id=user.id,
                    symbol=symbol,
                    status="BLOCKED",
                    public_reason_json={
                        "title": title,
                        "symbol": symbol,
                    },
                    internal_reason_json={
                        "source": source,
                        "plan": plan,
                        "error": detail,
                        "payload": exc.payload,
                        "context": context_extra or {},
                    },
                )
            )
        except Exception as exc:
            send_status = "FAILED"
            detail = str(exc)
            failed += 1
            db.add(
                SignalEvent(
                    user_id=user.id,
                    symbol=symbol,
                    status="BLOCKED",
                    public_reason_json={
                        "title": title,
                        "symbol": symbol,
                    },
                    internal_reason_json={
                        "source": source,
                        "plan": plan,
                        "error": detail,
                        "context": context_extra or {},
                    },
                )
            )

        db.add(
            DeliveryLog(
                run_id=run_id,
                user_id=user.id,
                symbol=symbol,
                source=source,
                tier=plan,
                subscription_status=sub.status,
                send_status=send_status,
                consume_status="CONSUMED" if send_status == "SENT" else "NOT_ATTEMPTED",
                detail=detail,
                context_json={
                    **context_json,
                    "used_after": context_json.get("usage", {}).get("used") if send_status == "SENT" else used,
                },
            )
        )

    return {
        "sent": sent,
        "failed": failed,
        "skipped_tier": skipped_tier,
        "skipped_quota": skipped_quota,
        "considered": len(recipients),
    }


def _acquire_dispatch_lock(db: Session, *, symbol: str, as_of_utc: datetime, tier_min: str) -> bool:
    lock_event = SignalEvent(
        user_id=None,
        symbol=symbol,
        status="DISPATCH_LOCK",
        tier_min=tier_min,
        snapshot_as_of_utc=as_of_utc,
        dispatch_kind="fast_trigger",
        public_reason_json={"symbol": symbol},
        internal_reason_json={
            "dispatch_kind": "fast_trigger",
            "symbol": symbol,
            "as_of_utc": _as_utc(as_of_utc).isoformat(),
            "tier_min": tier_min,
        },
    )
    db.add(lock_event)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


@router.post("/oracle/run")
def admin_run_oracle(
    payload: OracleRunIn,
    _admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    try:
        result = compute_dual_timeframe_snapshot(db, symbol=payload.symbol.strip().upper())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    snapshot, created = _upsert_snapshot(db, result)
    _upsert_weekly_range_from_result(db, result)
    db.commit()
    return _snapshot_response(snapshot, result, created)


@router.post("/signals/send")
def admin_send_signal(
    payload: AdminSignalSendIn,
    _admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    symbol = payload.symbol.strip().upper()
    run_id = uuid4()
    counts = _dispatch_to_telegram(
        db,
        run_id=run_id,
        symbol=symbol,
        tier_min=payload.tier_min,
        title=payload.title,
        message=payload.message,
        source="admin_signal_send",
    )
    db.commit()

    return {
        "ok": True,
        "run_id": str(run_id),
        "tier_min": payload.tier_min,
        "symbol": symbol,
        **counts,
    }


@router.post("/oracle/run-and-send")
def admin_run_and_send(
    payload: OracleRunIn,
    _admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    symbol = payload.symbol.strip().upper()
    try:
        result = compute_dual_timeframe_snapshot(db, symbol=symbol)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    snapshot, created = _upsert_snapshot(db, result)
    _upsert_weekly_range_from_result(db, result)
    db.commit()

    base_response = _snapshot_response(snapshot, result, created)
    if result["final_allowed_basic"] == "NO_TRADE" or not result["confirm_ok"]:
        return {
            **base_response,
            "send": {
                "attempted": False,
                "sent": 0,
                "failed": 0,
                "skipped_tier": 0,
                "skipped_quota": 0,
                "considered": 0,
            },
        }

    as_of_utc = _as_utc(result["as_of"])
    if not _acquire_dispatch_lock(db, symbol=symbol, as_of_utc=as_of_utc, tier_min="basic"):
        return {
            **base_response,
            "send": {
                "attempted": False,
                "duplicate_prevented": True,
                "sent": 0,
                "failed": 0,
                "skipped_tier": 0,
                "skipped_quota": 0,
                "considered": 0,
            },
        }

    run_id = uuid4()
    title = "Live Bias"
    message = (
        f"Direction: {result['final_allowed_basic']}\n"
        f"Confirmation: {result['confirm_tf']} confirmed\n"
        f"Confidence: {result['confidence'] * 100:.1f}%\n"
        f"As of (UTC): {as_of_utc.isoformat()}"
    )
    counts = _dispatch_to_telegram(
        db,
        run_id=run_id,
        symbol=symbol,
        tier_min="basic",
        title=title,
        message=message,
        source="fast_trigger",
        plan_exact="basic",
        context_extra={"as_of_utc": as_of_utc.isoformat(), "final_allowed": result["final_allowed_basic"]},
    )
    db.commit()

    return {
        **base_response,
        "send": {
            "attempted": True,
            "duplicate_prevented": False,
            "tier_min": "basic",
            "run_id": str(run_id),
            **counts,
        },
    }
