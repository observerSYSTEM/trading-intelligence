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
from app.services.data_provider import api_candle_mode, candle_provider_debug_labels, get_data_provider
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


def _provider_source_name(provider) -> str | None:
    primary = getattr(getattr(provider, "primary", None), "name", None)
    return str(primary or getattr(provider, "name", "") or "").strip() or None


def _provider_fallback_name(provider) -> str | None:
    fallback = getattr(getattr(provider, "fallback", None), "name", None)
    return str(fallback or "").strip() or None


def _anchor_lookup_provider(provider):
    primary = getattr(provider, "primary", None)
    if primary is not None and str(getattr(primary, "name", "") or "").strip().lower() == "oanda":
        return primary
    return provider


def _anchor_timeframe_delta(timeframe: str) -> timedelta:
    mapping = {
        "M1": timedelta(minutes=1),
        "M5": timedelta(minutes=5),
        "M15": timedelta(minutes=15),
    }
    return mapping[timeframe.strip().upper()]


def _complete_candles(candles: list) -> list:
    return [candle for candle in candles if bool(getattr(candle, "complete", True))]


def _candle_contains_target(candle, *, timeframe: str, target_utc: datetime) -> bool:
    candle_start = _as_utc(candle.time_utc)
    return candle_start <= target_utc < candle_start + _anchor_timeframe_delta(timeframe)


def _probe_api_candle(symbol: str, timeframe: str) -> dict:
    try:
        provider = get_data_provider()
        candle = provider.get_latest_closed_candle(symbol=symbol, timeframe=timeframe)
        candle_time = _as_utc(candle.time_utc)
        return {
            "ok": True,
            "time_utc": candle_time,
            "time_utc_iso": candle_time.isoformat(),
            "source": str(getattr(candle, "source", "") or _provider_source_name(provider) or "").strip() or None,
            "provider": _provider_source_name(provider),
            "fallback_provider": _provider_fallback_name(provider),
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "time_utc": None,
            "time_utc_iso": None,
            "source": None,
            "provider": None,
            "fallback_provider": None,
            "error": str(exc),
        }


def _probe_api_anchor_candle(symbol: str, *, target_utc: datetime) -> dict:
    try:
        raw_provider = get_data_provider()
    except Exception as exc:
        logger.warning(
            "api_anchor_lookup_m1_failed symbol=%s target_utc=%s provider=%s error=%s",
            symbol,
            target_utc.isoformat(),
            None,
            exc,
        )
        return {
            "ok": False,
            "status": "fetch_failed",
            "source": None,
            "provider": None,
            "fallback_provider": None,
            "timeframe": None,
            "fallback_timeframe": None,
            "time_utc": None,
            "time_utc_iso": None,
            "nearest_time_utc_iso": None,
            "nearest_delta_seconds": None,
            "count": 0,
            "selected_count": 0,
            "candle": None,
            "error": str(exc),
        }
    provider = _anchor_lookup_provider(raw_provider)
    provider_name = _provider_source_name(raw_provider) or _provider_source_name(provider)
    fallback_name = _provider_fallback_name(raw_provider)
    m1_count = 0
    nearest = None
    nearest_delta = None
    errors: list[str] = []

    def _remember_nearest(candles: list) -> None:
        nonlocal nearest, nearest_delta
        if not candles:
            return
        candidate = min(
            candles,
            key=lambda candle: (
                abs((_as_utc(candle.time_utc) - target_utc).total_seconds()),
                _as_utc(candle.time_utc),
            ),
        )
        candidate_delta = int(abs((_as_utc(candidate.time_utc) - target_utc).total_seconds()))
        if nearest is None or nearest_delta is None or candidate_delta < nearest_delta:
            nearest = candidate
            nearest_delta = candidate_delta

    def _ok(candle, *, status: str, timeframe: str, selected_count: int) -> dict:
        selected_time = _as_utc(candle.time_utc)
        source = str(getattr(candle, "source", "") or provider_name or "").strip() or None
        return {
            "ok": True,
            "status": status,
            "source": source,
            "provider": provider_name,
            "fallback_provider": fallback_name,
            "timeframe": timeframe,
            "fallback_timeframe": timeframe if timeframe != "M1" else None,
            "time_utc": selected_time,
            "time_utc_iso": selected_time.isoformat(),
            "nearest_time_utc_iso": _as_utc(nearest.time_utc).isoformat() if nearest is not None else selected_time.isoformat(),
            "nearest_delta_seconds": nearest_delta if nearest_delta is not None else 0,
            "count": m1_count,
            "selected_count": selected_count,
            "candle": candle,
            "error": None,
        }

    try:
        m1_candles = _complete_candles(
            provider.get_candles_range(
                symbol=symbol,
                timeframe="M1",
                start_utc=target_utc - timedelta(minutes=5),
                end_utc=target_utc + timedelta(minutes=7),
            )
        )
        m1_count = len(m1_candles)
        _remember_nearest(m1_candles)
        exact = next((candle for candle in m1_candles if _as_utc(candle.time_utc) == target_utc), None)
        if exact is not None:
            return _ok(exact, status="found", timeframe="M1", selected_count=m1_count)
        logger.warning(
            "api_anchor_lookup_m1_failed symbol=%s target_utc=%s count=%s provider=%s",
            symbol,
            target_utc.isoformat(),
            m1_count,
            provider_name,
        )
    except Exception as exc:
        errors.append(f"M1: {exc}")
        logger.warning(
            "api_anchor_lookup_m1_failed symbol=%s target_utc=%s provider=%s error=%s",
            symbol,
            target_utc.isoformat(),
            provider_name,
            exc,
        )

    for timeframe in ("M5", "M15"):
        try:
            delta = _anchor_timeframe_delta(timeframe)
            candles = _complete_candles(
                provider.get_candles_range(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_utc=target_utc - delta,
                    end_utc=target_utc + timedelta(seconds=1),
                )
            )
            _remember_nearest(candles)
            selected = next(
                (candle for candle in candles if _candle_contains_target(candle, timeframe=timeframe, target_utc=target_utc)),
                None,
            )
            if selected is None:
                errors.append(f"{timeframe}: no complete candle containing target")
                continue
            event_name = f"api_anchor_lookup_{timeframe.lower()}_fallback_ok"
            logger.info(
                "%s symbol=%s target_utc=%s candle_time_utc=%s provider=%s source=%s",
                event_name,
                symbol,
                target_utc.isoformat(),
                _as_utc(selected.time_utc).isoformat(),
                provider_name,
                getattr(selected, "source", None),
            )
            return _ok(
                selected,
                status=f"{timeframe.lower()}_fallback_ok",
                timeframe=timeframe,
                selected_count=len(candles),
            )
        except Exception as exc:
            errors.append(f"{timeframe}: {exc}")

    return {
        "ok": False,
        "status": "fetch_failed" if errors else "missing",
        "source": provider_name,
        "provider": provider_name,
        "fallback_provider": fallback_name,
        "timeframe": None,
        "fallback_timeframe": None,
        "time_utc": None,
        "time_utc_iso": None,
        "nearest_time_utc_iso": _as_utc(nearest.time_utc).isoformat() if nearest is not None else None,
        "nearest_delta_seconds": nearest_delta,
        "count": m1_count,
        "selected_count": 0,
        "candle": None,
        "error": "; ".join(errors) if errors else None,
    }


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
    api_mode = api_candle_mode()

    mt5_connected = False
    mt5_error: str | None = None
    latest_candle_source: str | None = None
    last_candle_time: str | None = None
    probe_symbol = symbols[0] if symbols else "XAUUSD"
    try:
        if api_mode:
            probe = _probe_api_candle(probe_symbol, "M1")
            mt5_connected = bool(probe.get("ok"))
            mt5_error = None if probe.get("ok") else str(probe.get("error") or "")
            latest_candle_source = probe.get("source")
            last_candle_time = probe.get("time_utc_iso")
        else:
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
            source: str | None = None
            stale = True
            if candle is not None:
                candle_time = _as_utc(candle.time_utc)
                latest_candle_utc = candle_time.isoformat()
                lag_seconds = max(int((now_value - candle_time).total_seconds()), 0)
                stale = lag_seconds > threshold
            if api_mode and stale:
                probe = _probe_api_candle(symbol_value, timeframe)
                if probe.get("ok") and isinstance(probe.get("time_utc"), datetime):
                    candle_time = probe["time_utc"]
                    latest_candle_utc = probe.get("time_utc_iso")
                    lag_seconds = max(int((now_value - candle_time).total_seconds()), 0)
                    stale = lag_seconds > threshold
                    source = probe.get("source")
                    if timeframe == "M1":
                        latest_candle_source = source or latest_candle_source
                        last_candle_time = latest_candle_utc or last_candle_time
            if stale:
                stale_reasons.append(f"{timeframe.lower()}_lag" if lag_seconds is not None else f"{timeframe.lower()}_missing")
            timeframes_payload.append(
                {
                    "timeframe": timeframe,
                    "latest_candle_utc": latest_candle_utc,
                    "latest_candle_source": source,
                    "lag_seconds": lag_seconds,
                    "stale": stale,
                }
            )

        symbol_stale = bool(stale_reasons)
        if not api_mode and ingest_lag_seconds is None:
            symbol_stale = True
            stale_reasons.append("ingest_missing")
        elif not api_mode and ingest_lag_seconds > threshold:
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
                "api_mode": api_mode,
                **candle_provider_debug_labels(
                    latest_candle_source=latest_candle_source,
                    last_candle_time=last_candle_time,
                    anchor_candle_source=None,
                    anchor_candle_status=None,
                ),
            }
        )

    return {
        "ok": True,
        "now_utc": now_value.isoformat(),
        "mt5_connected": mt5_connected,
        "mt5_error": mt5_error,
        "api_mode": api_mode,
        "provider_connected": mt5_connected,
        **candle_provider_debug_labels(
            latest_candle_source=latest_candle_source,
            last_candle_time=last_candle_time,
            anchor_candle_source=None,
            anchor_candle_status=None,
        ),
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
    api_mode = api_candle_mode()
    anchor_candle = None
    if actual_found_utc is not None:
        exact_candidates = (
            db.query(MT5Candle)
            .filter(
                MT5Candle.symbol == symbol_value,
                MT5Candle.timeframe == "M1",
                MT5Candle.time_utc >= actual_found_utc,
                MT5Candle.time_utc < actual_found_utc + timedelta(minutes=1),
            )
            .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
            .all()
        )
        if exact_candidates:
            anchor_candle = exact_candidates[0]

    api_anchor = (
        _probe_api_anchor_candle(symbol_value, target_utc=target_utc)
        if api_mode and anchor_candle is None and target_utc is not None
        else None
    )
    api_anchor_candle = api_anchor.get("candle") if isinstance(api_anchor, dict) else None
    displayed_candle = anchor_candle or api_anchor_candle
    displayed_candle_time = _as_utc(displayed_candle.time_utc) if displayed_candle is not None else None

    open_v = float(displayed_candle.open) if displayed_candle is not None else None
    high_v = float(displayed_candle.high) if displayed_candle is not None else None
    low_v = float(displayed_candle.low) if displayed_candle is not None else None
    close_v = float(displayed_candle.close) if displayed_candle is not None else None

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
    backfill_result = factors.get("backfill_result") if isinstance(factors.get("backfill_result"), dict) else None
    resolved_candle_symbol = (
        factors.get("resolved_candle_symbol")
        or (backfill_result.get("resolved_candle_symbol") if backfill_result else None)
        or (getattr(api_anchor_candle, "broker_symbol", None) if api_anchor_candle is not None else None)
        or factors.get("resolved_mt5_symbol")
        or (backfill_result.get("resolved_mt5_symbol") if backfill_result else None)
        or symbol_value
    )
    anchor_candle_source = (
        (api_anchor.get("source") if isinstance(api_anchor, dict) and api_anchor.get("source") else None)
        or factors.get("anchor_candle_source")
        or (backfill_result.get("anchor_candle_source") if backfill_result else None)
        or factors.get("nearest_available_candle_source")
        or factors.get("selection_source")
    )
    anchor_candle_status = (
        (api_anchor.get("status") if isinstance(api_anchor, dict) and api_anchor.get("status") else None)
        or factors.get("anchor_candle_status")
        or (backfill_result.get("anchor_candle_status") if backfill_result else None)
        or ("found" if displayed_candle is not None else "missing")
    )
    ingest_origin = "backfill" if backfill_attempted else "direct_ingest"
    if api_mode and api_anchor and api_anchor.get("ok") and anchor_candle is None:
        ingest_origin = "api_provider"
    if str(factors.get("selection_source") or "").lower() == "none" and ingest_origin != "api_provider":
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
            "candle_time_utc": displayed_candle_time.isoformat() if displayed_candle_time is not None else None,
            "candle_time_london": _to_london_iso(displayed_candle_time),
            "source": anchor_candle_source,
            "status": anchor_candle_status,
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
            "requested_symbol": factors.get("requested_symbol") or symbol_value,
            "resolved_mt5_symbol": factors.get("resolved_mt5_symbol")
            or (backfill_result.get("resolved_mt5_symbol") if backfill_result else None)
            or symbol_value,
            "resolved_candle_symbol": resolved_candle_symbol,
            "london_time_used": factors.get("target_london") or _to_london_iso(target_utc),
            "utc_time_used": factors.get("target_utc") or (target_utc.isoformat() if target_utc else None),
            "broker_server_time_utc": factors.get("broker_server_time_utc"),
            "expected_0801_broker_time": factors.get("expected_0801_broker_time"),
            "actual_candle_found_time": factors.get("actual_candle_found_time")
            or (api_anchor.get("time_utc_iso") if isinstance(api_anchor, dict) else None),
            "lookup_start_utc": factors.get("lookup_start_utc") or factors.get("search_start_utc"),
            "lookup_end_utc": factors.get("lookup_end_utc") or factors.get("search_end_utc"),
            "lookup_start_broker_utc": factors.get("lookup_start_broker_utc") or factors.get("search_start_broker_utc"),
            "lookup_end_broker_utc": factors.get("lookup_end_broker_utc") or factors.get("search_end_broker_utc"),
            "m1_candles_returned_utc_window": factors.get("m1_candles_returned_utc_window")
            or factors.get("candidate_count_utc_window")
            or (api_anchor.get("count") if isinstance(api_anchor, dict) else None),
            "m1_candles_returned_broker_window": factors.get("m1_candles_returned_broker_window")
            or factors.get("candidate_count_broker_window"),
            "m1_candles_returned_total": factors.get("m1_candles_returned_total")
            or factors.get("candidate_count_total")
            or (api_anchor.get("count") if isinstance(api_anchor, dict) else None),
            "nearest_available_candle_time": factors.get("nearest_available_candle_time")
            or (api_anchor.get("nearest_time_utc_iso") if isinstance(api_anchor, dict) else None),
            "nearest_available_candle_time_london": factors.get("nearest_available_candle_time_london")
            or (
                _to_london_iso(_parse_iso_utc(api_anchor.get("nearest_time_utc_iso")))
                if isinstance(api_anchor, dict)
                else None
            ),
            "nearest_available_candle_source": factors.get("nearest_available_candle_source")
            or (api_anchor.get("source") if isinstance(api_anchor, dict) else None),
            "nearest_available_candle_delta_seconds": factors.get("nearest_available_candle_delta_seconds")
            or (api_anchor.get("nearest_delta_seconds") if isinstance(api_anchor, dict) else None),
            "selection_source": factors.get("selection_source") or ("api_provider" if ingest_origin == "api_provider" else None),
            "ingest_origin": ingest_origin,
            "selection_tolerance_seconds": factors.get("selection_tolerance_seconds"),
            "selected_time_delta_seconds": factors.get("selected_time_delta_seconds")
            or (api_anchor.get("nearest_delta_seconds") if isinstance(api_anchor, dict) else None),
            "backfill_attempted": backfill_attempted,
            "backfill_result": backfill_result,
            "api_mode": api_mode,
            "api_candle_error": api_anchor.get("error") if isinstance(api_anchor, dict) else None,
            **candle_provider_debug_labels(
                latest_candle_source=None,
                last_candle_time=None,
                anchor_candle_source=anchor_candle_source,
                anchor_candle_status=anchor_candle_status,
            ),
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
