from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.admin_ops import build_readiness_payload
from app.api.deps import require_admin
from app.core.symbols import enabled_symbols_from_settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import DailyPermissionSnapshot, GoldRegimeDaily, MT5Candle, MT5IngestStatus, User, UserSignalPref
from app.db.session import get_db
from app.services.data_provider import get_data_provider
from app.services.telegram import send_telegram_message

router = APIRouter(prefix="/ops", tags=["ops"])
logger = logging.getLogger(__name__)
LONDON_TZ = ZoneInfo("Europe/London")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _to_london_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).astimezone(LONDON_TZ).isoformat()


def _resolve_anchor_final_allowed(db: Session, *, symbol: str, permission_row: DailyPermissionSnapshot) -> str:
    symbol_value = symbol.strip().upper()
    permission_as_of = _as_utc(permission_row.as_of_utc)
    permission_for_date = permission_row.for_date or permission_row.date_uk
    fallback = str(permission_row.daily_permission or "NO_TRADE").strip().upper() or "NO_TRADE"

    candidates = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == symbol_value)
        .order_by(GoldRegimeDaily.as_of_utc.desc(), GoldRegimeDaily.created_at.desc())
        .limit(50)
        .all()
    )
    for snapshot in candidates:
        public = snapshot.public_factors_json if isinstance(snapshot.public_factors_json, dict) else {}
        matches_as_of = False
        if isinstance(public.get("daily_permission_as_of_utc"), str):
            parsed_as_of = _parse_iso_utc(public.get("daily_permission_as_of_utc"))
            matches_as_of = parsed_as_of == permission_as_of if parsed_as_of is not None else False

        matches_for_date = False
        if permission_for_date is not None:
            matches_for_date = str(public.get("permission_for_date_uk") or "").strip() == permission_for_date.isoformat()

        if not (matches_as_of or matches_for_date):
            continue

        final_allowed = str(
            snapshot.final_allowed_elite
            or snapshot.final_allowed_basic
            or public.get("final_allowed_elite")
            or public.get("final_allowed_basic")
            or snapshot.allowed_direction
            or fallback
        ).strip().upper()
        if final_allowed:
            return final_allowed

    return fallback


class TelegramTestIn(BaseModel):
    chat_id: str | None = Field(default=None, min_length=5, max_length=32, pattern=r"^-?\d+$")
    text: str | None = Field(default=None, min_length=1, max_length=4000)


@router.get("/ready")
def ops_ready(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("ops_ready", (RateLimitRule(limit=30, window_seconds=60),)),
):
    return build_readiness_payload(db)


@router.get("/market/status")
def ops_market_status(
    symbol: str | None = None,
    stale_after_seconds: int = 300,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("ops_market_status", (RateLimitRule(limit=60, window_seconds=60),)),
):
    now_value = datetime.now(timezone.utc)
    threshold = max(int(stale_after_seconds), 30)
    symbols = [symbol.strip().upper()] if symbol else enabled_symbols_from_settings()

    mt5_connected = False
    mt5_error: str | None = None
    probe_symbol = symbols[0] if symbols else "XAUUSD"
    try:
        provider = get_data_provider()
        provider.get_latest_closed_candle(symbol=probe_symbol, timeframe="M1")
        mt5_connected = True
    except Exception as exc:
        mt5_error = str(exc)

    items: list[dict] = []
    for symbol_value in symbols:
        ingest_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol_value).first()
        ingest_lag_seconds: int | None = None
        last_ingested_at_utc: str | None = None
        if ingest_row is not None:
            last_ingested = _as_utc(ingest_row.last_ingested_at)
            last_ingested_at_utc = last_ingested.isoformat()
            ingest_lag_seconds = max(int((now_value - last_ingested).total_seconds()), 0)

        timeframes_payload: list[dict] = []
        stale_reasons: list[str] = []
        for timeframe in ("M1", "M15", "H1"):
            candle = (
                db.query(MT5Candle)
                .filter(MT5Candle.symbol == symbol_value, MT5Candle.timeframe == timeframe)
                .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
                .first()
            )
            latest_candle_utc: str | None = None
            lag_seconds: int | None = None
            stale = True
            if candle is not None:
                candle_time = _as_utc(candle.time_utc)
                latest_candle_utc = candle_time.isoformat()
                lag_seconds = max(int((now_value - candle_time).total_seconds()), 0)
                stale = lag_seconds > threshold
            if stale:
                stale_reasons.append(f"{timeframe.lower()}_lag" if lag_seconds is not None else f"{timeframe.lower()}_missing")
            timeframes_payload.append(
                {
                    "timeframe": timeframe,
                    "latest_candle_utc": latest_candle_utc,
                    "lag_seconds": lag_seconds,
                    "stale": stale,
                }
            )

        symbol_stale = bool(stale_reasons)
        if ingest_lag_seconds is None:
            symbol_stale = True
            stale_reasons.append("ingest_missing")
        elif ingest_lag_seconds > threshold:
            symbol_stale = True
            stale_reasons.append("ingest_lag")
        broker_offset_seconds = int(ingest_row.broker_offset_seconds) if ingest_row and ingest_row.broker_offset_seconds is not None else 0
        broker_server_time_utc = (now_value + timedelta(seconds=broker_offset_seconds)).isoformat()

        if symbol_stale:
            logger.warning(
                "market status stale symbol=%s reasons=%s ingest_lag_seconds=%s threshold=%s",
                symbol_value,
                ",".join(stale_reasons),
                ingest_lag_seconds,
                threshold,
            )

        items.append(
            {
                "symbol": symbol_value,
                "now_utc": now_value.isoformat(),
                "last_ingested_at_utc": last_ingested_at_utc,
                "ingest_lag_seconds": ingest_lag_seconds,
                "broker_offset_seconds": broker_offset_seconds,
                "broker_offset_hours": round(float(broker_offset_seconds) / 3600.0, 4),
                "broker_server_time_utc": broker_server_time_utc,
                "latest_candle_utc": {row["timeframe"]: row["latest_candle_utc"] for row in timeframes_payload},
                "timeframes": timeframes_payload,
                "stale": symbol_stale,
                "stale_reasons": stale_reasons,
            }
        )

    return {
        "ok": True,
        "now_utc": now_value.isoformat(),
        "mt5_connected": mt5_connected,
        "mt5_error": mt5_error,
        "stale": any(item["stale"] for item in items),
        "stale_after_seconds": threshold,
        "items": items,
    }


@router.get("/anchor-debug")
def ops_anchor_debug(
    symbol: str = "XAUUSD",
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("ops_anchor_debug", (RateLimitRule(limit=60, window_seconds=60),)),
):
    symbol_value = symbol.strip().upper()
    row = (
        db.query(DailyPermissionSnapshot)
        .filter(
            DailyPermissionSnapshot.symbol == symbol_value,
            DailyPermissionSnapshot.daily_permission_stage == "OFFICIAL",
        )
        .order_by(
            DailyPermissionSnapshot.for_date.desc(),
            DailyPermissionSnapshot.as_of_utc.desc(),
            DailyPermissionSnapshot.created_at.desc(),
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail=f"No OFFICIAL daily permission found for {symbol_value}")

    factors = row.factors_json if isinstance(row.factors_json, dict) else {}
    target_utc = _parse_iso_utc(factors.get("target_utc") if isinstance(factors.get("target_utc"), str) else None)
    permission_candle_close_utc = _parse_iso_utc(
        factors.get("permission_candle_close_utc") if isinstance(factors.get("permission_candle_close_utc"), str) else None
    )
    actual_found_utc = _parse_iso_utc(
        factors.get("actual_candle_found_time") if isinstance(factors.get("actual_candle_found_time"), str) else None
    )
    lookup_utc = actual_found_utc or permission_candle_close_utc or target_utc or _as_utc(row.as_of_utc)

    nearest_candle = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol_value,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= lookup_utc - timedelta(minutes=5),
            MT5Candle.time_utc <= lookup_utc + timedelta(minutes=5),
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )
    anchor_candle = None
    if nearest_candle:
        anchor_candle = min(
            nearest_candle,
            key=lambda c: abs((_as_utc(c.time_utc) - lookup_utc).total_seconds()),
        )

    open_v = float(anchor_candle.open) if anchor_candle is not None else None
    high_v = float(anchor_candle.high) if anchor_candle is not None else None
    low_v = float(anchor_candle.low) if anchor_candle is not None else None
    close_v = float(anchor_candle.close) if anchor_candle is not None else None

    if open_v is None and isinstance(factors.get("open"), (int, float)):
        open_v = float(factors.get("open"))
    if close_v is None and isinstance(factors.get("close"), (int, float)):
        close_v = float(factors.get("close"))
    if high_v is None and open_v is not None and close_v is not None and isinstance(factors.get("range"), (int, float)):
        half_range = float(factors.get("range")) / 2.0
        high_v = max(open_v, close_v) + half_range
    if low_v is None and open_v is not None and close_v is not None and isinstance(factors.get("range"), (int, float)):
        half_range = float(factors.get("range")) / 2.0
        low_v = min(open_v, close_v) - half_range

    anchor_direction = "UNKNOWN"
    if open_v is not None and close_v is not None:
        if close_v > open_v:
            anchor_direction = "BULL"
        elif close_v < open_v:
            anchor_direction = "BEAR"

    body_size = abs(close_v - open_v) if open_v is not None and close_v is not None else None
    wick_size = (
        max((high_v - low_v) - body_size, 0.0)
        if high_v is not None and low_v is not None and body_size is not None
        else None
    )

    final_allowed = _resolve_anchor_final_allowed(db, symbol=symbol_value, permission_row=row)

    backfill_attempted = bool(factors.get("backfill_attempted"))
    ingest_origin = "backfill" if backfill_attempted else "direct_ingest"
    if str(factors.get("selection_source") or "").lower() == "none":
        ingest_origin = "unknown"

    return {
        "ok": True,
        "symbol": symbol_value,
        "anchor": {
            "direction": anchor_direction,
            "open": open_v,
            "high": high_v,
            "low": low_v,
            "close": close_v,
            "body_size": body_size,
            "wick_size": wick_size,
            "candle_time_utc": _as_utc(anchor_candle.time_utc).isoformat() if anchor_candle is not None else None,
            "candle_time_london": _to_london_iso(_as_utc(anchor_candle.time_utc)) if anchor_candle is not None else None,
        },
        "official_permission": {
            "daily_permission": row.daily_permission,
            "permission_source": row.permission_source,
            "permission_lock_time": factors.get("target_london") or _to_london_iso(target_utc),
            "last_refreshed_at": _as_utc(row.computed_at_utc).isoformat(),
            "permission_time_utc": _as_utc(row.as_of_utc).isoformat(),
            "permission_time_london": _to_london_iso(_as_utc(row.as_of_utc)),
            "final_allowed": final_allowed,
        },
        "time_mapping": {
            "london_time_used": factors.get("target_london") or _to_london_iso(target_utc),
            "utc_time_used": factors.get("target_utc") or (target_utc.isoformat() if target_utc else None),
            "broker_server_time_utc": factors.get("broker_server_time_utc"),
            "expected_0801_broker_time": factors.get("expected_0801_broker_time"),
            "actual_candle_found_time": factors.get("actual_candle_found_time"),
            "selection_source": factors.get("selection_source"),
            "ingest_origin": ingest_origin,
            "backfill_attempted": backfill_attempted,
            "backfill_result": factors.get("backfill_result"),
        },
        "explanations": [
            "Anchor direction is based on close vs open.",
            "Permission Lock Time is the official 08:01 lock.",
            "Last Refreshed At is only the latest recompute/display time.",
        ],
    }


@router.post("/telegram/test")
def ops_telegram_test(
    payload: TelegramTestIn | None = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("ops_telegram_test", (RateLimitRule(limit=20, window_seconds=60),)),
):
    requested_chat_id = ((payload.chat_id if payload else None) or "").strip()
    if requested_chat_id:
        chat_id = requested_chat_id
    else:
        pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == admin.id).first()
        chat_id = (pref.telegram_chat_id or "").strip() if pref else ""

    if not chat_id:
        raise HTTPException(
            status_code=400,
            detail="No telegram_chat_id saved yet. Go to Settings → Telegram.",
        )

    text = ((payload.text if payload else None) or "").strip()
    if not text:
        text = "✅ Telegram test OK — your bot can deliver signals."

    try:
        sent = send_telegram_message(chat_id, text, disable_preview=True)
    except Exception as exc:
        logger.exception("ops telegram test failed admin_id=%s", admin.id)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": sent.get("message_id"),
    }
