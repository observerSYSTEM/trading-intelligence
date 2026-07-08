from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_runner_auth
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import GoldRegimeDaily, MT5Candle, MT5IngestStatus
from app.db.session import get_db
from app.services.data_provider import api_candle_mode
from app.services.oracle_basic import oracle_from_candle
from app.services.targets_refresh import maybe_refresh_targets_on_magnet_hit, recompute_targets_snapshot

router = APIRouter(prefix="/ingest/mt5", tags=["ingest (mt5)"])
logger = logging.getLogger(__name__)


class MT5CandleIn(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D1"] = "M1"
    candle_time_utc: datetime
    candle_time_london: datetime | None = None
    candle_time_broker: datetime | None = None
    broker_utc_offset_minutes: int | None = None
    o: float = Field(..., description="Open")
    h: float = Field(..., description="High")
    l: float = Field(..., description="Low")
    c: float = Field(..., description="Close")
    tick_volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    source: str = "mt5_runner"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_of_day_utc(value: datetime) -> datetime:
    v = _as_utc(value)
    return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)


def _regime_from_direction(direction: str) -> str:
    if direction == "BUY_ONLY":
        return "bullish"
    if direction == "SELL_ONLY":
        return "bearish"
    return "range"


def _confidence_from_candle(o: float, h: float, l: float, c: float) -> float:
    candle_range = max(h - l, 0.000001)
    body = abs(c - o)
    ratio = min(max(body / candle_range, 0.0), 1.0)
    return round(ratio, 4)


@router.post("/candle")
def ingest_mt5_candle(
    payload: MT5CandleIn,
    _runner_ip: str = Depends(require_runner_auth),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("ingest_mt5_candle", (RateLimitRule(limit=240, window_seconds=60),)),
):
    symbol = payload.symbol.strip().upper()
    timeframe = payload.timeframe.strip().upper()
    as_of_utc = _as_utc(payload.candle_time_utc)
    candle_time_london = _as_utc(payload.candle_time_london) if payload.candle_time_london else None
    candle_time_broker = _as_utc(payload.candle_time_broker) if payload.candle_time_broker else None

    candle = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == timeframe,
            MT5Candle.time_utc == as_of_utc,
        )
        .first()
    )
    candle_created = candle is None
    if candle is None:
        candle = MT5Candle(
            symbol=symbol,
            timeframe=timeframe,
            time_utc=as_of_utc,
        )
        db.add(candle)

    candle.open = payload.o
    candle.high = payload.h
    candle.low = payload.l
    candle.close = payload.c
    candle.volume = payload.tick_volume

    decision = oracle_from_candle(
        symbol=symbol,
        o=payload.o,
        h=payload.h,
        l=payload.l,
        c=payload.c,
    )
    confidence = _confidence_from_candle(payload.o, payload.h, payload.l, payload.c)
    as_of_day_utc = _as_of_day_utc(as_of_utc)
    compute_now_utc = datetime.now(timezone.utc)

    snapshot = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == symbol, GoldRegimeDaily.as_of_utc == as_of_day_utc)
        .first()
    )
    snapshot_created = snapshot is None
    if snapshot is None:
        snapshot = GoldRegimeDaily(symbol=symbol, as_of_utc=as_of_day_utc)
        db.add(snapshot)

    snapshot.regime = _regime_from_direction(decision.direction)
    snapshot.confidence = confidence
    snapshot.allowed_direction = decision.direction
    snapshot.notes = "Computed from latest closed MT5 candle"
    snapshot.public_factors_json = {
        "timeframe": timeframe,
        "candle_body_ratio": confidence,
        "last_compute_at_utc": compute_now_utc.isoformat(),
        "latest_candle_time_utc": as_of_utc.isoformat(),
    }
    snapshot.internal_factors_json = {
        "source": payload.source,
        "timeframe": timeframe,
        "candle_time_utc": as_of_utc.isoformat(),
        "candle_time_london": candle_time_london.isoformat() if candle_time_london else None,
        "candle_time_broker": candle_time_broker.isoformat() if candle_time_broker else None,
        "broker_utc_offset_minutes": payload.broker_utc_offset_minutes,
        "o": payload.o,
        "h": payload.h,
        "l": payload.l,
        "c": payload.c,
        "tick_volume": payload.tick_volume,
        "reason": decision.reason,
    }

    status_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol).first()
    now_value = datetime.now(timezone.utc)
    payload_offset_seconds = (
        int(payload.broker_utc_offset_minutes) * 60
        if payload.broker_utc_offset_minutes is not None
        else None
    )
    broker_offset_seconds = payload_offset_seconds
    if broker_offset_seconds is None and status_row is not None and status_row.broker_offset_seconds is not None:
        broker_offset_seconds = int(status_row.broker_offset_seconds)
    if broker_offset_seconds is None:
        broker_offset_seconds = 0
    if status_row is None:
        status_row = MT5IngestStatus(
            symbol=symbol,
            last_ingested_at=as_of_utc,
            broker_offset_seconds=broker_offset_seconds,
            broker_offset_detected_at=now_value,
        )
        db.add(status_row)
    else:
        if _as_utc(status_row.last_ingested_at) < as_of_utc:
            status_row.last_ingested_at = as_of_utc
        status_row.broker_offset_seconds = int(broker_offset_seconds)
        if payload_offset_seconds is not None:
            status_row.broker_offset_detected_at = now_value

    db.commit()
    source_value = str(payload.source or "").strip().lower()
    if api_candle_mode() or source_value in {"oanda", "twelvedata", "api_candle_runner_loop"}:
        logger.info(
            "api_candle_ingest_write_ok symbol=%s timeframe=%s candle_time_utc=%s source=%s created=%s",
            symbol,
            timeframe,
            as_of_utc.isoformat(),
            payload.source,
            candle_created,
        )

    if timeframe == "H1":
        try:
            recompute_targets_snapshot(
                db,
                symbol=symbol,
                tier="pro",
                price_bid=payload.bid if payload.bid is not None else payload.c,
                price_ask=payload.ask if payload.ask is not None else payload.c,
                as_of_utc=datetime.now(timezone.utc),
                reason="h1_close",
            )
            recompute_targets_snapshot(
                db,
                symbol=symbol,
                tier="elite",
                price_bid=payload.bid if payload.bid is not None else payload.c,
                price_ask=payload.ask if payload.ask is not None else payload.c,
                as_of_utc=datetime.now(timezone.utc),
                reason="h1_close",
            )
            db.commit()
            logger.info("targets recompute on H1 close symbol=%s", symbol)
        except Exception:
            db.rollback()
            logger.exception("targets recompute on H1 close failed symbol=%s", symbol)

    if timeframe == "M1":
        try:
            bid = payload.bid if payload.bid is not None else payload.c
            ask = payload.ask if payload.ask is not None else payload.c
            hit_res_pro = maybe_refresh_targets_on_magnet_hit(
                db,
                symbol=symbol,
                bid=float(bid),
                ask=float(ask),
                m1_close=float(payload.c),
                event_time_utc=datetime.now(timezone.utc),
                tier="pro",
            )
            hit_res_elite = maybe_refresh_targets_on_magnet_hit(
                db,
                symbol=symbol,
                bid=float(bid),
                ask=float(ask),
                m1_close=float(payload.c),
                event_time_utc=datetime.now(timezone.utc),
                tier="elite",
            )
            db.commit()
            if hit_res_pro.get("hit") or hit_res_elite.get("hit"):
                logger.info(
                    "magnet hit processed symbol=%s pro=%s elite=%s",
                    symbol,
                    hit_res_pro.get("hit"),
                    hit_res_elite.get("hit"),
                )
        except Exception:
            db.rollback()
            logger.exception("magnet hit refresh failed symbol=%s", symbol)

    return {
        "ok": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "candle_time_utc": as_of_utc.isoformat(),
        "as_of_day_utc": as_of_day_utc.isoformat(),
        "candle_created": candle_created,
        "snapshot_created": snapshot_created,
        "allowed_direction": decision.direction,
        "confidence": confidence,
        "broker_offset_seconds": broker_offset_seconds,
    }
