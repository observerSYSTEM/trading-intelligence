from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import (
    GoldPositioningSnapshot,
    GoldRegimeDaily,
    GoldStressIntraday,
    User,
)
from app.db.session import get_db
from app.services.audit import log_audit

router = APIRouter(prefix="/admin/gold", tags=["admin (gold)"])


def _as_utc(value: datetime | None) -> datetime:
    ts = value or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


class RegimeIn(BaseModel):
    symbol: str = "XAUUSD"
    as_of_utc: datetime | None = None
    regime: Literal["bullish", "bearish", "range"]
    confidence: float = Field(..., ge=0, le=1)
    allowed_direction: Literal["BUY_ONLY", "SELL_ONLY", "NO_TRADE"]
    notes: str | None = None
    public_factors: dict[str, float] = Field(default_factory=dict)
    internal_factors: dict[str, float] = Field(default_factory=dict)


class PositioningIn(BaseModel):
    symbol: str = "XAUUSD"
    as_of_utc: datetime | None = None
    cot_net_non_commercial: int | None = None
    comex_open_interest: int | None = None
    gld_flow_tonnes: float | None = None
    iau_flow_tonnes: float | None = None
    crowding_score: float = Field(..., ge=0, le=100)
    positioning_bias: Literal["bullish", "bearish", "neutral"]
    squeeze_risk: Literal["low", "medium", "high"]
    contra_signal: bool = False
    public_factors: dict[str, float] = Field(default_factory=dict)
    internal_factors: dict[str, float] = Field(default_factory=dict)


class StressIn(BaseModel):
    symbol: str = "XAUUSD"
    as_of_utc: datetime | None = None
    basis_bps: float | None = None
    front_month_spread_bps: float | None = None
    spread_volatility_bps: float | None = None
    inventory_stress_score: float | None = None
    stress_score: float = Field(..., ge=0, le=100)
    state: Literal["green", "amber", "red"]
    execution_guidance: Literal["normal", "reduce_size", "avoid"]
    public_factors: dict[str, float] = Field(default_factory=dict)
    internal_factors: dict[str, float] = Field(default_factory=dict)


@router.post("/regime")
def upsert_regime(
    payload: RegimeIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_gold_regime", (RateLimitRule(limit=30, window_seconds=60),)),
):
    log_audit(db, action="admin.gold.regime.upsert", user_id=_admin.id, request=request, meta={"symbol": payload.symbol})
    as_of = _as_utc(payload.as_of_utc)
    row = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == payload.symbol, GoldRegimeDaily.as_of_utc == as_of)
        .first()
    )
    created = row is None
    if row is None:
        row = GoldRegimeDaily(symbol=payload.symbol, as_of_utc=as_of)
        db.add(row)

    row.regime = payload.regime
    row.confidence = payload.confidence
    row.allowed_direction = payload.allowed_direction
    row.notes = payload.notes
    row.public_factors_json = payload.public_factors
    row.internal_factors_json = payload.internal_factors

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "created": created,
        "data": {
            "id": str(row.id),
            "symbol": row.symbol,
            "as_of_utc": _iso(row.as_of_utc),
            "regime": row.regime,
            "confidence": row.confidence,
            "allowed_direction": row.allowed_direction,
            "notes": row.notes,
            "public_factors": row.public_factors_json or {},
            "internal_factors": row.internal_factors_json or {},
            "created_at": _iso(row.created_at),
        },
    }


@router.post("/positioning")
def upsert_positioning(
    payload: PositioningIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_gold_positioning", (RateLimitRule(limit=30, window_seconds=60),)),
):
    log_audit(
        db,
        action="admin.gold.positioning.upsert",
        user_id=_admin.id,
        request=request,
        meta={"symbol": payload.symbol},
    )
    as_of = _as_utc(payload.as_of_utc)
    row = (
        db.query(GoldPositioningSnapshot)
        .filter(
            GoldPositioningSnapshot.symbol == payload.symbol,
            GoldPositioningSnapshot.as_of_utc == as_of,
        )
        .first()
    )
    created = row is None
    if row is None:
        row = GoldPositioningSnapshot(symbol=payload.symbol, as_of_utc=as_of)
        db.add(row)

    row.cot_net_non_commercial = payload.cot_net_non_commercial
    row.comex_open_interest = payload.comex_open_interest
    row.gld_flow_tonnes = payload.gld_flow_tonnes
    row.iau_flow_tonnes = payload.iau_flow_tonnes
    row.crowding_score = payload.crowding_score
    row.positioning_bias = payload.positioning_bias
    row.squeeze_risk = payload.squeeze_risk
    row.contra_signal = payload.contra_signal
    row.public_factors_json = payload.public_factors
    row.internal_factors_json = payload.internal_factors

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "created": created,
        "data": {
            "id": str(row.id),
            "symbol": row.symbol,
            "as_of_utc": _iso(row.as_of_utc),
            "crowding_score": row.crowding_score,
            "positioning_bias": row.positioning_bias,
            "squeeze_risk": row.squeeze_risk,
            "contra_signal": row.contra_signal,
            "public_factors": row.public_factors_json or {},
            "internal_factors": row.internal_factors_json or {},
            "created_at": _iso(row.created_at),
        },
    }


@router.post("/stress")
def upsert_stress(
    payload: StressIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_gold_stress", (RateLimitRule(limit=30, window_seconds=60),)),
):
    log_audit(db, action="admin.gold.stress.upsert", user_id=_admin.id, request=request, meta={"symbol": payload.symbol})
    as_of = _as_utc(payload.as_of_utc)
    row = (
        db.query(GoldStressIntraday)
        .filter(GoldStressIntraday.symbol == payload.symbol, GoldStressIntraday.as_of_utc == as_of)
        .first()
    )
    created = row is None
    if row is None:
        row = GoldStressIntraday(symbol=payload.symbol, as_of_utc=as_of)
        db.add(row)

    row.basis_bps = payload.basis_bps
    row.front_month_spread_bps = payload.front_month_spread_bps
    row.spread_volatility_bps = payload.spread_volatility_bps
    row.inventory_stress_score = payload.inventory_stress_score
    row.stress_score = payload.stress_score
    row.state = payload.state
    row.execution_guidance = payload.execution_guidance
    row.public_factors_json = payload.public_factors
    row.internal_factors_json = payload.internal_factors

    db.commit()
    db.refresh(row)

    return {
        "ok": True,
        "created": created,
        "data": {
            "id": str(row.id),
            "symbol": row.symbol,
            "as_of_utc": _iso(row.as_of_utc),
            "stress_score": row.stress_score,
            "state": row.state,
            "execution_guidance": row.execution_guidance,
            "public_factors": row.public_factors_json or {},
            "internal_factors": row.internal_factors_json or {},
            "created_at": _iso(row.created_at),
        },
    }
