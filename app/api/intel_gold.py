from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import (
    GoldPositioningSnapshot,
    GoldRegimeDaily,
    GoldStressIntraday,
    Subscription,
    User,
)
from app.db.session import get_db
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage

router = APIRouter(prefix="/intel/gold", tags=["intel (gold)"])

ACTIVE_STATUSES = {"active", "trialing"}
TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def _latest_by_symbol(db: Session, model, symbol: str):
    return (
        db.query(model)
        .filter(model.symbol == symbol)
        .order_by(model.as_of_utc.desc(), model.created_at.desc())
        .first()
    )


def _ensure_subscription(db: Session, user: User) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if sub:
        return sub

    sub = Subscription(user_id=user.id, plan="basic", status="inactive", usage_count=0)
    db.add(sub)
    db.commit()
    db.refresh(sub)
    return sub


def _ensure_active(user: User, sub: Subscription) -> None:
    if getattr(user, "role", "user") == "admin":
        return
    status = (sub.status or "").lower()
    if status not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=402,
            detail=f"Subscription not active (status={sub.status or 'inactive'})",
        )


def _user_plan(user: User, sub: Subscription) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    plan = (sub.plan or "basic").lower()
    if plan not in TIER_ORDER:
        return "basic"
    return plan


def _require_min_tier(plan: str, minimum: str) -> None:
    if TIER_ORDER.get(plan, 0) < TIER_ORDER[minimum]:
        raise HTTPException(status_code=403, detail=f"{minimum.upper()} plan required")


def _consume_if_requested(db: Session, user: User, consume: bool, symbol: str) -> dict:
    usage = get_usage(db, user.id)
    if not consume:
        db.commit()
        return {**usage, "consumed": False}
    try:
        usage_after = consume_usage(
            db,
            user.id,
            n=1,
            reason="intel_gold_api",
            symbol=symbol,
            meta={"path": "/intel/gold/today"},
        )
        db.commit()
        return {**usage_after, "consumed": True}
    except UsageLimitExceeded as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=exc.payload) from exc


@router.get("/regime/latest")
def get_regime_latest(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = _ensure_subscription(db, user)
    _ensure_active(user, sub)

    row = _latest_by_symbol(db, GoldRegimeDaily, symbol)
    if not row:
        raise HTTPException(status_code=404, detail="No gold regime data available")

    return {
        "symbol": row.symbol,
        "as_of_utc": _iso(row.as_of_utc),
        "regime": row.regime,
        "confidence": row.confidence,
        "allowed_direction": row.allowed_direction,
        "notes": row.notes,
    }


@router.get("/positioning/latest")
def get_positioning_latest(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = _ensure_subscription(db, user)
    _ensure_active(user, sub)
    plan = _user_plan(user, sub)
    _require_min_tier(plan, "pro")

    row = _latest_by_symbol(db, GoldPositioningSnapshot, symbol)
    if not row:
        raise HTTPException(status_code=404, detail="No gold positioning data available")

    return {
        "symbol": row.symbol,
        "as_of_utc": _iso(row.as_of_utc),
        "positioning_bias": row.positioning_bias,
        "crowding_score": row.crowding_score,
        "squeeze_risk": row.squeeze_risk,
        "contra_signal": row.contra_signal,
    }


@router.get("/stress/latest")
def get_stress_latest(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = _ensure_subscription(db, user)
    _ensure_active(user, sub)
    plan = _user_plan(user, sub)
    _require_min_tier(plan, "elite")

    row = _latest_by_symbol(db, GoldStressIntraday, symbol)
    if not row:
        raise HTTPException(status_code=404, detail="No gold stress data available")

    return {
        "symbol": row.symbol,
        "as_of_utc": _iso(row.as_of_utc),
        "stress_score": row.stress_score,
        "state": row.state,
        "execution_guidance": row.execution_guidance,
    }


@router.get("/today")
def get_gold_today_pack(
    symbol: str = "XAUUSD",
    consume: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = _ensure_subscription(db, user)
    _ensure_active(user, sub)
    plan = _user_plan(user, sub)

    usage = _consume_if_requested(
        db,
        user=user,
        consume=consume and getattr(user, "role", "user") != "admin",
        symbol=symbol,
    )

    regime = _latest_by_symbol(db, GoldRegimeDaily, symbol)
    if not regime:
        raise HTTPException(status_code=404, detail="No gold regime data available")

    payload = {
        "symbol": regime.symbol,
        "as_of_utc": _iso(regime.as_of_utc),
        "allowed_direction": regime.allowed_direction,
        "confidence": regime.confidence,
        "headline": f"London bias: {regime.allowed_direction}",
        "tier": plan,
        "usage": usage,
    }

    if plan in {"pro", "elite"}:
        positioning = _latest_by_symbol(db, GoldPositioningSnapshot, symbol)
        if positioning:
            payload["positioning"] = {
                "as_of_utc": _iso(positioning.as_of_utc),
                "positioning_bias": positioning.positioning_bias,
                "crowding_score": positioning.crowding_score,
                "squeeze_risk": positioning.squeeze_risk,
                "contra_signal": positioning.contra_signal,
            }

    if plan == "elite":
        stress = _latest_by_symbol(db, GoldStressIntraday, symbol)
        if stress:
            payload["stress"] = {
                "as_of_utc": _iso(stress.as_of_utc),
                "stress_score": stress.stress_score,
                "state": stress.state,
                "execution_guidance": stress.execution_guidance,
            }
            payload["news_mode"] = stress.state in {"amber", "red"}

    return payload
