from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.api.intel_gold import get_gold_today_pack
from app.core.config import settings
from app.core.symbols import allowed_symbols_for_plan, normalize_plan
from app.db.models import (
    DeliveryLog,
    DailyPermissionSnapshot,
    GoldRegimeDaily,
    MarketStateDaily,
    MT5Candle,
    MT5IngestStatus,
    NotificationRoute,
    OraclePermissionDaily,
    OracleQuarterlySnapshot,
    OracleTargetsSnapshot,
    Subscription,
    RunnerStatus,
    User,
    UserSignalPref,
    WeeklyRangeSnapshot,
)
from app.db.session import get_db
from app.services.data_provider import get_data_provider
from app.services.oracle_basic import oracle_from_candle
from app.services.oracle_snapshot import compute_dual_timeframe_snapshot, regime_from_direction
from app.services.session_intel import get_symbol_session_context
from app.services.strategy_matrix import DAILY_BIAS, StrategyMatrixError, validate_symbol_for_strategy
from app.services.symbol_preferences import get_user_enabled_symbols
from app.services.telegram import send_telegram_message
from app.services.audit import log_audit
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage

router = APIRouter(
    prefix="/oracle",
    tags=["oracle"],
    dependencies=[
        rate_limit(
            "oracle_api",
            (
                RateLimitRule(limit=120, window_seconds=60),
                RateLimitRule(limit=2000, window_seconds=3600),
            ),
        )
    ],
)

logger = logging.getLogger(__name__)


def _uk_tz() -> tuple[timezone | ZoneInfo, bool]:
    try:
        return ZoneInfo("Europe/London"), True
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401

            return ZoneInfo("Europe/London"), True
        except Exception:
            logger.warning("Europe/London timezone unavailable in oracle API; falling back to UTC.")
            return timezone.utc, False


UK_TZ, UK_TZ_AVAILABLE = _uk_tz()


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
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _snapshot_compute_time(snapshot: GoldRegimeDaily | None) -> datetime | None:
    if snapshot is None:
        return None
    public = snapshot.public_factors_json if isinstance(snapshot.public_factors_json, dict) else {}
    raw = public.get("last_compute_at_utc")
    if isinstance(raw, str):
        parsed = _parse_iso_utc(raw)
        if parsed:
            return parsed
    if snapshot.created_at:
        return _as_utc(snapshot.created_at)
    return _as_utc(snapshot.as_of_utc)


def _latest_daily_permission_snapshot(
    db: Session,
    symbol: str,
    *,
    date_uk=None,
    stage: str | None = None,
) -> DailyPermissionSnapshot | None:
    query = db.query(DailyPermissionSnapshot).filter(DailyPermissionSnapshot.symbol == symbol)
    if date_uk is not None:
        query = query.filter(DailyPermissionSnapshot.date_uk == date_uk)
    if stage:
        query = query.filter(DailyPermissionSnapshot.daily_permission_stage == stage)
    return query.order_by(DailyPermissionSnapshot.as_of_utc.desc(), DailyPermissionSnapshot.created_at.desc()).first()


def _daily_permission_health(db: Session, symbol: str, *, now_utc: datetime) -> dict:
    if not UK_TZ_AVAILABLE:
        return {
            "timezone": "UTC_FALLBACK",
            "degraded": True,
            "reason": "Europe/London timezone unavailable; 08:01 logic disabled.",
            "reason_code": "tz_mismatch",
            "target_utc": None,
            "candle_time_utc": None,
            "missing": True,
            "backfill_attempted": False,
            "backfill_result": None,
            "for_date_uk": None,
        }

    now_local = now_utc.astimezone(UK_TZ)
    active_date = now_local.date()
    target_local = datetime(
        active_date.year,
        active_date.month,
        active_date.day,
        8,
        1,
        tzinfo=UK_TZ,
    )
    target_utc = target_local.astimezone(timezone.utc)

    row = _latest_daily_permission_snapshot(db, symbol, date_uk=active_date, stage="OFFICIAL")
    prelim_row = _latest_daily_permission_snapshot(db, symbol, date_uk=active_date, stage="PRELIM")
    ingest_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol).first()
    factors = row.factors_json if (row and isinstance(row.factors_json, dict)) else {}
    runner_row = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()
    runner_last_error = str(factors.get("runner_last_error") or "").strip() or None
    if not runner_last_error and runner_row is not None and not bool(runner_row.mt5_connected):
        runner_last_error = str(runner_row.last_error or "").strip() or "Runner MT5 disconnected."
    backfill_attempted = bool(factors.get("backfill_attempted"))
    backfill_result = factors.get("backfill_result") if isinstance(factors.get("backfill_result"), dict) else None
    candle_time_utc = _parse_iso_utc(factors.get("candle_time_utc")) if isinstance(factors.get("candle_time_utc"), str) else None
    actual_candle_found_time = _parse_iso_utc(factors.get("actual_candle_found_time")) if isinstance(factors.get("actual_candle_found_time"), str) else None
    expected_0801_broker_time = _parse_iso_utc(factors.get("expected_0801_broker_time")) if isinstance(factors.get("expected_0801_broker_time"), str) else None
    broker_offset_seconds = factors.get("broker_offset_seconds")
    if broker_offset_seconds is None and ingest_row and ingest_row.broker_offset_seconds is not None:
        broker_offset_seconds = ingest_row.broker_offset_seconds
    if broker_offset_seconds is None:
        broker_offset_seconds = 0
    try:
        broker_offset_seconds = int(broker_offset_seconds)
    except Exception:
        broker_offset_seconds = 0
    broker_offset_hours = round(float(broker_offset_seconds) / 3600.0, 4)
    broker_server_time_utc = (
        _parse_iso_utc(factors.get("broker_server_time_utc")) if isinstance(factors.get("broker_server_time_utc"), str) else None
    )
    if broker_server_time_utc is None:
        broker_server_time_utc = now_utc + timedelta(seconds=int(broker_offset_seconds))
    if expected_0801_broker_time is None:
        expected_0801_broker_time = target_utc + timedelta(seconds=int(broker_offset_seconds))
    missing = row is None or bool(factors.get("missing_data"))
    future = candle_time_utc is not None and candle_time_utc > (now_utc + timedelta(seconds=30))

    degraded = False
    reason = None
    reason_code = None
    if now_local.date() == active_date and (now_local.hour, now_local.minute) >= (8, 20):
        if missing:
            degraded = True
            reason = "08:01 candle not available yet."
            reason_code = "missing_0801"
        elif future:
            degraded = True
            reason = "08:01 candle timestamp is in the future."
            reason_code = "tz_mismatch"

    if row and row.reason and (missing or future):
        reason = row.reason
    if reason and runner_last_error and "runner" not in reason.lower():
        reason = f"{reason} Runner error: {runner_last_error}"
    permission_stage = "OFFICIAL" if row is not None else ("PRELIM" if prelim_row is not None else None)
    permission_source = None
    permission_as_of = None
    if row is not None:
        permission_source = str(row.permission_source or "LONDON_0801").upper()
        permission_as_of = _as_utc(row.as_of_utc).isoformat()
    elif prelim_row is not None:
        permission_source = str(prelim_row.permission_source or "ASIA").upper()
        permission_as_of = _as_utc(prelim_row.as_of_utc).isoformat()
    return {
        "timezone": "Europe/London",
        "degraded": degraded,
        "reason": reason,
        "reason_code": reason_code,
        "target_utc": target_utc.isoformat(),
        "target_london": target_local.isoformat(),
        "candle_time_utc": candle_time_utc.isoformat() if candle_time_utc else None,
        "actual_candle_found_time": actual_candle_found_time.isoformat() if actual_candle_found_time else None,
        "expected_0801_broker_time": expected_0801_broker_time.isoformat() if expected_0801_broker_time else None,
        "broker_offset_seconds": int(broker_offset_seconds),
        "broker_offset_hours": broker_offset_hours,
        "broker_server_time_utc": broker_server_time_utc.isoformat() if broker_server_time_utc else None,
        "missing": bool(missing),
        "date_uk": active_date.isoformat(),
        "for_date_uk": active_date.isoformat(),
        "backfill_attempted": backfill_attempted,
        "backfill_result": backfill_result,
        "permission_stage": permission_stage,
        "permission_source": permission_source,
        "permission_as_of_utc": permission_as_of,
        "permission_lock_time_london": target_local.isoformat(),
        "runner_last_error": runner_last_error,
    }


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


def _http_detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail)
    if detail is None:
        return "HTTPException"
    return str(detail)


def _format_user_outcome_message(pack: dict) -> str:
    confidence = pack.get("confidence")
    if isinstance(confidence, (int, float)):
        conf_text = f"{confidence * 100:.0f}%"
    else:
        conf_text = "N/A"
    symbol = str(pack.get("symbol", "XAUUSD"))

    lines = [
        f"<b>London {symbol} Intel</b>",
        f"<b>Symbol:</b> {symbol}",
        f"<b>Bias:</b> {pack.get('allowed_direction', 'NO_TRADE')}",
        f"<b>Confidence:</b> {conf_text}",
        f"<b>Headline:</b> {pack.get('headline', '')}",
    ]

    positioning = pack.get("positioning")
    if isinstance(positioning, dict):
        lines.extend(
            [
                "",
                "<b>Positioning (Pro+)</b>",
                f"- Bias: {positioning.get('positioning_bias', 'n/a')}",
                f"- Crowding: {positioning.get('crowding_score', 'n/a')}",
                f"- Squeeze risk: {positioning.get('squeeze_risk', 'n/a')}",
            ]
        )

    stress = pack.get("stress")
    if isinstance(stress, dict):
        lines.extend(
            [
                "",
                "<b>Stress (Elite)</b>",
                f"- Score: {stress.get('stress_score', 'n/a')}",
                f"- State: {stress.get('state', 'n/a')}",
                f"- Guidance: {stress.get('execution_guidance', 'n/a')}",
            ]
        )
        if pack.get("news_mode"):
            lines.append("- News mode: elevated")

    lines.extend(
        [
            "",
            "<i>Rule:</i> Follow the allowed direction only.",
        ]
    )
    return "\n".join(lines)


def _upsert_gold_regime_from_oracle(
    db: Session,
    *,
    symbol: str,
    as_of_utc: datetime,
    direction: str,
    reason: str,
    o: float,
    h: float,
    l: float,
    c: float,
    provider_name: str,
    timeframe: str,
) -> None:
    row = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == symbol, GoldRegimeDaily.as_of_utc == as_of_utc)
        .first()
    )
    if row is None:
        row = GoldRegimeDaily(symbol=symbol, as_of_utc=as_of_utc)
        db.add(row)

    row.regime = _regime_from_direction(direction)
    row.allowed_direction = direction
    row.confidence = _confidence_from_candle(o=o, h=h, l=l, c=c)
    row.notes = reason
    row.public_factors_json = {
        "candle_body_ratio": row.confidence,
    }
    row.internal_factors_json = {
        "engine": "daily_oracle",
        "reason": reason,
        "data_provider": provider_name,
        "timeframe": timeframe,
        "candle_time_utc": as_of_utc.isoformat(),
        "o": o,
        "h": h,
        "l": l,
        "c": c,
    }


def _latest_snapshot(db: Session, symbol: str) -> GoldRegimeDaily | None:
    return (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == symbol)
        .order_by(GoldRegimeDaily.as_of_utc.desc(), GoldRegimeDaily.created_at.desc())
        .first()
    )


def _latest_ingest_status(db: Session, symbol: str) -> MT5IngestStatus | None:
    return db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol).first()


def _latest_candle(db: Session, symbol: str, timeframe: str | None = None) -> MT5Candle | None:
    query = db.query(MT5Candle).filter(MT5Candle.symbol == symbol)
    if timeframe:
        query = query.filter(MT5Candle.timeframe == timeframe)
    return query.order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc()).first()


def _resolve_plan(db: Session, user: User) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return normalize_plan(sub.plan if sub else "basic")


def _selected_symbols_for_user(db: Session, user: User, plan: str) -> list[str]:
    return get_user_enabled_symbols(db, user.id, plan)


def _latest_quarterly_snapshot(db: Session, symbol: str) -> OracleQuarterlySnapshot | None:
    return (
        db.query(OracleQuarterlySnapshot)
        .filter(OracleQuarterlySnapshot.symbol == symbol)
        .order_by(OracleQuarterlySnapshot.as_of_utc.desc(), OracleQuarterlySnapshot.created_at.desc())
        .first()
    )


def _today_permission_decision(db: Session, symbol: str, date_uk_value: datetime) -> OraclePermissionDaily | None:
    return (
        db.query(OraclePermissionDaily)
        .filter(
            OraclePermissionDaily.symbol == symbol,
            OraclePermissionDaily.date_uk == date_uk_value.date(),
        )
        .order_by(OraclePermissionDaily.as_of_utc.desc(), OraclePermissionDaily.created_at.desc())
        .first()
    )


def _latest_permission_decision(db: Session, symbol: str) -> OraclePermissionDaily | None:
    return (
        db.query(OraclePermissionDaily)
        .filter(OraclePermissionDaily.symbol == symbol)
        .order_by(OraclePermissionDaily.as_of_utc.desc(), OraclePermissionDaily.created_at.desc())
        .first()
    )


def _latest_weekly_range_snapshot(db: Session, symbol: str) -> WeeklyRangeSnapshot | None:
    return (
        db.query(WeeklyRangeSnapshot)
        .filter(WeeklyRangeSnapshot.symbol == symbol)
        .order_by(WeeklyRangeSnapshot.as_of_utc.desc(), WeeklyRangeSnapshot.created_at.desc())
        .first()
    )


def _latest_targets_snapshot(db: Session, symbol: str) -> OracleTargetsSnapshot | None:
    row = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == "pro")
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .first()
    )
    if row:
        return row
    return (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol)
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .first()
    )


def _london_0801_window_debug(db: Session, *, symbol: str, for_date_uk: date) -> dict:
    target_local = datetime(for_date_uk.year, for_date_uk.month, for_date_uk.day, 8, 1, tzinfo=UK_TZ)
    target_utc = target_local.astimezone(timezone.utc)
    search_start_utc = target_utc - timedelta(minutes=3)
    search_end_utc = target_utc + timedelta(minutes=5) + timedelta(minutes=1)

    rows_utc = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= search_start_utc,
            MT5Candle.time_utc < search_end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )

    ingest_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol).first()
    broker_offset_seconds = int(ingest_row.broker_offset_seconds or 0) if ingest_row else 0
    target_broker_utc = target_utc + timedelta(seconds=broker_offset_seconds)
    search_start_broker_utc = search_start_utc + timedelta(seconds=broker_offset_seconds)
    search_end_broker_utc = search_end_utc + timedelta(seconds=broker_offset_seconds)
    rows_broker = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= search_start_broker_utc,
            MT5Candle.time_utc < search_end_broker_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )

    selected = None
    selected_source = None
    if rows_utc:
        selected = min(rows_utc, key=lambda row: abs((_as_utc(row.time_utc) - target_utc).total_seconds()))
        selected_source = "utc_window"
    elif rows_broker:
        selected = min(rows_broker, key=lambda row: abs((_as_utc(row.time_utc) - target_broker_utc).total_seconds()))
        selected_source = "broker_window"

    to_item = lambda row: {  # noqa: E731
        "time_utc": _as_utc(row.time_utc).isoformat(),
        "time_london": _as_utc(row.time_utc).astimezone(UK_TZ).isoformat(),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
    }

    payload = {
        "symbol": symbol,
        "for_date_uk": for_date_uk.isoformat(),
        "target_0801_london": target_local.isoformat(),
        "target_0801_utc": target_utc.isoformat(),
        "search_start_utc": search_start_utc.isoformat(),
        "search_end_utc": (search_end_utc - timedelta(minutes=1)).isoformat(),
        "broker_offset_seconds": broker_offset_seconds,
        "expected_0801_broker_time": target_broker_utc.isoformat(),
        "search_start_broker_utc": search_start_broker_utc.isoformat(),
        "search_end_broker_utc": (search_end_broker_utc - timedelta(minutes=1)).isoformat(),
        "utc_window_candles": [to_item(row) for row in rows_utc],
        "broker_window_candles": [to_item(row) for row in rows_broker],
        "selected_source": selected_source,
        "selected_0801": to_item(selected) if selected is not None else None,
    }
    logger.info(
        "oracle 0801 debug symbol=%s for_date_uk=%s utc_candidates=%s broker_candidates=%s selected_source=%s selected_time=%s",
        symbol,
        for_date_uk.isoformat(),
        len(rows_utc),
        len(rows_broker),
        selected_source,
        payload["selected_0801"]["time_utc"] if payload["selected_0801"] else None,
    )
    return payload


def _merge_targets_snapshot(data: dict, *, db: Session, symbol: str) -> dict:
    row = _latest_targets_snapshot(db, symbol)
    if not row:
        return data
    payload = dict(data)
    payload["liquidity_magnet"] = row.magnet_price
    payload["zone_to_zone_target"] = row.zone_to_zone_target
    targets_json = payload.get("targets_json") if isinstance(payload.get("targets_json"), dict) else {}
    targets_json = {
        **targets_json,
        "magnet_price": row.magnet_price,
        "zone_to_zone_target": row.zone_to_zone_target,
        "sellside_liquidity": row.sellside_liquidity,
        "buyside_liquidity": row.buyside_liquidity,
    }
    payload["targets_json"] = targets_json
    payload["targets_snapshot_as_of_utc"] = row.as_of_utc.isoformat()
    payload["targets_magnet_state"] = row.magnet_state if isinstance(row.magnet_state, dict) else {}
    return payload


def _base_from_live(live: dict, *, message: str) -> dict:
    as_of_iso = live["as_of"].isoformat()
    symbol = live["symbol"]
    return {
        "symbol": symbol,
        "title": f"{symbol} Daily Bias Snapshot",
        "as_of": as_of_iso,
        "as_of_utc": as_of_iso,
        "computed_at": as_of_iso,
        "last_compute_at_utc": as_of_iso,
        "timeframes": live["timeframes"],
        "timeframe": live["timeframes"]["signal"],
        "fast_bias": live["fast_bias"],
        "confirm_tf": live["confirm_tf"],
        "confirm_ok": live["confirm_ok"],
        "bias_m1": live["fast_bias"],
        "confirm_h1": live["confirm_ok"],
        "daily_permission": live.get("daily_permission", live["fast_bias"]),
        "daily_permission_as_of_utc": as_of_iso,
        "permission_stage": live.get("permission_stage", "OFFICIAL"),
        "permission_source": live.get("permission_source", "LONDON_0801"),
        "permission_lock_time_london": live.get("permission_lock_time_london"),
        "permission_for_date_uk": live.get("permission_for_date_uk"),
        "conflict_with_prelim": bool(live.get("conflict_with_prelim", False)),
        "conflict_note": live.get("conflict_note"),
        "opportunity_direction": live.get("opportunity_direction", live["fast_bias"]),
        "confidence": live["confidence"],
        "reason_basic": live["reason_basic"],
        "message": message,
        "final_allowed_basic": live["final_allowed_basic"],
        "final_allowed_elite": live["final_allowed_elite"],
        "daily_bias": live["daily_bias"],
        "daily_alignment": live["daily_alignment"],
        "news_gate_pass": live["news_gate_pass"],
        "news_blocked_window": live["news_blocked_window"],
        "risk_gate_pass": live["risk_gate_pass"],
        "atr_h1": live["atr_h1"],
        "adr_d1": live["adr_d1"],
        "volume_state": live["volume_state"],
        "liquidity_magnet": live["next_liquidity_magnet"],
        "zone_to_zone_target": live["zone_to_zone_target"],
        "targets_json": live["targets_json"],
        "candle": live["candle"],
        "quarter_context": live.get("quarter_context"),
        "quarterly_bias": live.get("quarterly_bias"),
        "permission_alignment": live.get("permission_alignment"),
        "message_tag": live.get("message_tag"),
        "ny_context_active": bool(live.get("ny_context_active", False)),
        "ny_note": live.get("ny_note"),
        "ny_confidence_delta": live.get("ny_confidence_delta"),
        "risk_banner": live.get("risk_banner") if isinstance(live.get("risk_banner"), dict) else {},
        "weekly_range": live.get("weekly_range") if isinstance(live.get("weekly_range"), dict) else {},
    }


def _base_from_snapshot(snapshot: GoldRegimeDaily, db: Session) -> dict:
    public = snapshot.public_factors_json if isinstance(snapshot.public_factors_json, dict) else {}
    timeframes = public.get("timeframes") if isinstance(public.get("timeframes"), dict) else None
    if not timeframes:
        timeframes = {
            "signal": str(public.get("signal_timeframe") or "M15"),
            "confirm": str(public.get("confirm_timeframe") or public.get("confirm_tf") or "H1"),
            "daily": "M1",
        }
    final_basic = snapshot.final_allowed_basic or public.get("final_allowed_basic") or snapshot.allowed_direction
    final_elite = snapshot.final_allowed_elite or public.get("final_allowed_elite") or final_basic
    as_of_dt = _as_utc(snapshot.as_of_utc)
    as_of_iso = as_of_dt.isoformat()
    compute_dt = _snapshot_compute_time(snapshot) or as_of_dt
    compute_iso = compute_dt.isoformat()
    now_utc = datetime.now(timezone.utc)
    daily_permission_as_of_raw = public.get("daily_permission_as_of_utc")
    daily_permission_as_of = None
    if isinstance(daily_permission_as_of_raw, str):
        parsed_permission_as_of = _parse_iso_utc(daily_permission_as_of_raw)
        if parsed_permission_as_of and parsed_permission_as_of <= (now_utc + timedelta(seconds=30)):
            daily_permission_as_of = parsed_permission_as_of.isoformat()
    timeframe = timeframes.get("signal", "M1")
    candle = _latest_candle(db, symbol=snapshot.symbol, timeframe=timeframe)
    risk_banner = public.get("risk_banner") if isinstance(public.get("risk_banner"), dict) else {}
    weekly_range = public.get("weekly_range") if isinstance(public.get("weekly_range"), dict) else {}
    if not weekly_range:
        weekly_row = _latest_weekly_range_snapshot(db, snapshot.symbol)
        if weekly_row:
            weekly_range = {
                "symbol": weekly_row.symbol,
                "week_key": weekly_row.week_key,
                "week_start_uk": weekly_row.week_start_uk.isoformat(),
                "high": weekly_row.high,
                "low": weekly_row.low,
                "mid": weekly_row.mid,
                "range_ready": bool(weekly_row.range_ready),
                "status": "Locked" if weekly_row.range_ready else "Building",
                "as_of_utc": weekly_row.as_of_utc.isoformat(),
                "meta_json": weekly_row.meta_json or {},
            }

    return {
        "symbol": snapshot.symbol,
        "title": f"{snapshot.symbol} Daily Bias Snapshot",
        "as_of": as_of_iso,
        "as_of_utc": as_of_iso,
        "computed_at": compute_iso,
        "last_compute_at_utc": compute_iso,
        "timeframes": timeframes,
        "timeframe": timeframe,
        "fast_bias": public.get("fast_bias", public.get("opportunity_direction", public.get("bias_m1", final_basic))),
        "confirm_tf": public.get("confirm_tf", str(timeframes.get("confirm", "H1"))),
        "confirm_ok": bool(public.get("confirm_ok", public.get("confirm_h1", snapshot.confirm_ok or False))),
        "bias_m1": public.get("bias_m1", public.get("daily_permission", final_basic)),
        "confirm_h1": bool(public.get("confirm_h1", public.get("confirm_ok", snapshot.confirm_ok or False))),
        "daily_permission": public.get("daily_permission", final_basic),
        "daily_permission_as_of_utc": daily_permission_as_of,
        "permission_stage": public.get("permission_stage"),
        "permission_source": public.get("permission_source"),
        "permission_lock_time_london": public.get("permission_lock_time_london"),
        "permission_for_date_uk": public.get("permission_for_date_uk"),
        "conflict_with_prelim": bool(public.get("conflict_with_prelim", False)),
        "conflict_note": public.get("conflict_note"),
        "opportunity_direction": public.get("opportunity_direction", public.get("fast_bias")),
        "confidence": snapshot.confidence,
        "reason_basic": public.get("reason_basic", snapshot.notes or "Latest snapshot ready."),
        "message": snapshot.notes or "Latest snapshot ready.",
        "final_allowed_basic": final_basic,
        "final_allowed_elite": final_elite,
        "daily_bias": snapshot.daily_bias or public.get("daily_bias", "neutral"),
        "daily_alignment": bool(public.get("daily_alignment", True)),
        "news_gate_pass": bool(public.get("news_gate_pass", True)),
        "news_blocked_window": public.get("news_blocked_window"),
        "risk_gate_pass": bool(public.get("risk_gate_pass", True)),
        "atr_h1": public.get("atr_h1"),
        "adr_d1": public.get("adr_d1"),
        "volume_state": public.get("volume_state"),
        "liquidity_magnet": public.get("next_liquidity_magnet"),
        "zone_to_zone_target": public.get("zone_to_zone_target"),
        "targets_json": public.get("targets_json", {}),
        "quarter_context": public.get("quarter_context"),
        "quarterly_bias": public.get("quarterly_bias"),
        "permission_alignment": public.get("permission_alignment"),
        "message_tag": public.get("message_tag"),
        "ny_context_active": bool(public.get("ny_context_active", False)),
        "ny_note": public.get("ny_note"),
        "ny_confidence_delta": public.get("ny_confidence_delta"),
        "risk_banner": risk_banner,
        "weekly_range": weekly_range,
        "candle": (
            {
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            if candle
            else None
        ),
    }


def _tier_shape(data: dict, plan: str) -> dict:
    final_allowed = data["final_allowed_elite"] if plan == "elite" else data["final_allowed_basic"]
    payload = {
        "symbol": data["symbol"],
        "title": data["title"],
        "as_of": data["as_of"],
        "as_of_utc": data["as_of_utc"],
        "computed_at": data["computed_at"],
        "last_compute_at_utc": data.get("last_compute_at_utc", data["computed_at"]),
        "timeframes": data["timeframes"],
        "timeframe": data["timeframe"],
        "final_allowed": final_allowed,
        "direction": final_allowed,
        "allowed_direction": final_allowed,
        "daily_permission": data.get("daily_permission", final_allowed),
        "daily_permission_as_of_utc": data.get("daily_permission_as_of_utc"),
        "permission_stage": data.get("permission_stage"),
        "permission_source": data.get("permission_source"),
        "permission_lock_time_london": data.get("permission_lock_time_london"),
        "permission_for_date_uk": data.get("permission_for_date_uk"),
        "conflict_with_prelim": bool(data.get("conflict_with_prelim", False)),
        "conflict_note": data.get("conflict_note"),
        "opportunity_direction": data.get("opportunity_direction", data.get("fast_bias")),
        "confidence": data["confidence"],
        "reason": data["reason_basic"],
        "message": data["reason_basic"],
        "regime": regime_from_direction(final_allowed),
        "headline": f"Bias: {final_allowed}",
        "plan_view": plan,
        "quarter_context": data.get("quarter_context"),
        "quarterly_bias": data.get("quarterly_bias"),
        "permission_alignment": data.get("permission_alignment"),
        "message_tag": data.get("message_tag"),
        "ny_context_active": bool(data.get("ny_context_active", False)),
        "ny_note": data.get("ny_note"),
        "ny_confidence_delta": data.get("ny_confidence_delta"),
        "risk_banner": data.get("risk_banner") or {},
        "weekly_range": data.get("weekly_range") or {},
    }
    if plan in {"pro", "elite"}:
        payload["fast_bias"] = data["fast_bias"]
        payload["confirm_tf"] = data["confirm_tf"]
        payload["confirm_ok"] = data["confirm_ok"]
        payload["bias_m1"] = data["bias_m1"]
        payload["confirm_h1"] = data["confirm_h1"]
        payload["liquidity_magnet"] = data["liquidity_magnet"]
        payload["zone_to_zone_target"] = data["zone_to_zone_target"]
        payload["targets_json"] = data["targets_json"] or {}
        payload["targets_as_of_utc"] = data.get("targets_snapshot_as_of_utc")
        payload["targets_magnet_state"] = data.get("targets_magnet_state") or {}
        payload["candle"] = data["candle"]
    if plan == "elite":
        payload["daily_bias"] = data["daily_bias"]
        payload["daily_alignment"] = data["daily_alignment"]
        payload["news_gate"] = {
            "pass": data["news_gate_pass"],
            "blocked_window": data["news_blocked_window"],
        }
        payload["risk_stats"] = {
            "atr_h1": data["atr_h1"],
            "adr_d1": data["adr_d1"],
            "risk_gate_pass": data["risk_gate_pass"],
        }
        payload["volume_state"] = data["volume_state"]
    return payload


@router.get("/quarterly/snapshot")
def get_quarterly_snapshot(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _resolve_plan(db, user)
    symbol_value = symbol.strip().upper()
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    if symbol_value not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not available on your tier")
    if symbol_value not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not enabled in your settings")

    row = _latest_quarterly_snapshot(db, symbol_value)
    if not row:
        raise HTTPException(status_code=404, detail="Quarterly snapshot not available yet")

    return {
        "symbol": row.symbol,
        "quarter_key": row.quarter_key,
        "quarter_open": row.quarter_open,
        "q_high_to_date": row.q_high,
        "q_low_to_date": row.q_low,
        "q_mid_to_date": row.q_mid,
        "premium_discount": row.premium_discount,
        "quarterly_bias": row.quarterly_bias,
        "permission_mode": row.permission_mode,
        "conflict_rule": row.conflict_rule,
        "confidence": row.confidence,
        "factors": row.factors_json or {},
        "as_of_utc": row.as_of_utc.isoformat(),
    }


@router.get("/permission/today")
def get_permission_today(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _resolve_plan(db, user)
    symbol_value = symbol.strip().upper()
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    if symbol_value not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not available on your tier")
    if symbol_value not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not enabled in your settings")

    now_uk = datetime.now(UK_TZ)
    row = _today_permission_decision(db, symbol_value, now_uk) or _latest_permission_decision(db, symbol_value)
    if not row:
        raise HTTPException(status_code=404, detail="Permission decision not available yet")

    details = row.details_json if isinstance(row.details_json, dict) else {}
    final_for_tier = details.get("allowed_direction_final_strict", row.allowed_direction_final)
    if plan in {"pro", "elite"}:
        final_for_tier = details.get("allowed_direction_final_soft", row.allowed_direction_final)

    return {
        "symbol": row.symbol,
        "date_uk": row.date_uk.isoformat(),
        "daily_bias_raw": row.daily_bias_raw,
        "quarterly_bias": row.quarterly_bias,
        "alignment": row.alignment,
        "allowed_direction_final": row.allowed_direction_final,
        "allowed_direction_final_strict": details.get("allowed_direction_final_strict", row.allowed_direction_final),
        "allowed_direction_final_soft": details.get("allowed_direction_final_soft", row.allowed_direction_final),
        "allowed_direction_for_tier": final_for_tier,
        "confidence_final": row.confidence_final,
        "message_tag": row.message_tag,
        "as_of_utc": row.as_of_utc.isoformat(),
        "details": details,
    }


@router.get("/latest")
def get_latest_oracle_snapshot(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _resolve_plan(db, user)
    symbol_value = symbol.strip().upper()
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    if symbol_value not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not available on your tier")
    if symbol_value not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not enabled in your settings")

    snapshot = _latest_snapshot(db, symbol_value)
    if snapshot:
        data = _base_from_snapshot(snapshot, db)
        if not data.get("permission_stage"):
            health = _daily_permission_health(db, symbol_value, now_utc=datetime.now(timezone.utc))
            data["permission_stage"] = health.get("permission_stage")
            data["permission_source"] = health.get("permission_source")
            data["permission_lock_time_london"] = health.get("permission_lock_time_london")
        # Backfill stale snapshots that were stored before richer target/risk fields existed.
        try_live_refresh = (not data.get("targets_json")) or (data.get("atr_h1") is None)
        if try_live_refresh:
            try:
                live = compute_dual_timeframe_snapshot(db, symbol=symbol_value)
                data = _base_from_live(live, message=live["reason_basic"])
            except ValueError as exc:
                logger.warning("live snapshot refresh skipped symbol=%s reason=%s", symbol_value, exc)
            except Exception:
                logger.exception("live snapshot refresh failed symbol=%s", symbol_value)
        data = _merge_targets_snapshot(data, db=db, symbol=symbol_value)
        return _tier_shape(data, plan)

    try:
        live = compute_dual_timeframe_snapshot(db, symbol=symbol_value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"No oracle snapshot available yet: {exc}") from exc

    data = _base_from_live(live, message=live["reason_basic"])
    data = _merge_targets_snapshot(data, db=db, symbol=symbol_value)
    return _tier_shape(data, plan)


@router.get("/session-context")
def get_oracle_session_context(
    symbol: str = "GBPJPY",
    as_of_utc: datetime | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _resolve_plan(db, user)
    symbol_value = symbol.strip().upper()
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    if symbol_value not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not available on your tier")
    if symbol_value not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not enabled in your settings")

    try:
        return get_symbol_session_context(db, symbol=symbol_value, as_of_utc=as_of_utc)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/snapshot/latest")
def get_latest_oracle_snapshot_contract(
    symbol: str = "XAUUSD",
    stale_after_minutes: int = 20,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    payload = get_latest_oracle_snapshot(symbol=symbol, user=user, db=db)
    symbol_value = symbol.strip().upper()
    now_utc = datetime.now(timezone.utc)

    ingest_row = _latest_ingest_status(db, symbol_value)
    compute_row = _latest_snapshot(db, symbol_value)
    latest_m15 = _latest_candle(db, symbol=symbol_value, timeframe="M15")
    last_ingest = _as_utc(ingest_row.last_ingested_at) if ingest_row else None
    last_compute = _snapshot_compute_time(compute_row)
    last_candle_close = _as_utc(latest_m15.time_utc) if latest_m15 else None
    compute_age_seconds = max(int((now_utc - last_compute).total_seconds()), 0) if last_compute else None
    ingest_age_seconds = max(int((now_utc - last_ingest).total_seconds()), 0) if last_ingest else None
    timeframe_seconds = 15 * 60
    stale_threshold_seconds = 300
    candle_age_seconds = max(int((now_utc - last_candle_close).total_seconds()), 0) if last_candle_close else None
    stale_reasons: list[str] = []

    def _add_reason(reason: str) -> None:
        if reason not in stale_reasons:
            stale_reasons.append(reason)

    if last_ingest is None:
        _add_reason("mt5_down")
    elif ingest_age_seconds is not None and ingest_age_seconds > stale_threshold_seconds:
        _add_reason("ingest_lag")
    if last_candle_close is None:
        _add_reason("m15_missing")
    permission_health = _daily_permission_health(db, symbol_value, now_utc=now_utc)
    is_stale = len(stale_reasons) > 0
    if is_stale:
        logger.warning(
            "oracle snapshot stale symbol=%s reasons=%s ingest_age_seconds=%s compute_age_seconds=%s",
            symbol_value,
            ",".join(stale_reasons),
            ingest_age_seconds,
            compute_age_seconds,
        )

    out = dict(payload)
    if isinstance(out.get("daily_permission_as_of_utc"), str):
        parsed_daily = _parse_iso_utc(out.get("daily_permission_as_of_utc"))
        if parsed_daily and parsed_daily > (now_utc + timedelta(seconds=30)):
            out["daily_permission_as_of_utc"] = None

    out.update(
        {
            "timeframe_main": "M15",
            "timeframe_fast": "M1",
            "last_ingest_at": last_ingest.isoformat() if last_ingest else None,
            "last_ingest_at_utc": last_ingest.isoformat() if last_ingest else None,
            "last_compute_at": last_compute.isoformat() if last_compute else None,
            "last_compute_at_utc": last_compute.isoformat() if last_compute else None,
            "latest_candle_close_utc": last_candle_close.isoformat() if last_candle_close else None,
            "timeframe_seconds": timeframe_seconds,
            "compute_age_seconds": compute_age_seconds,
            "ingest_age_seconds": ingest_age_seconds,
            "candle_age_seconds": candle_age_seconds,
            "age_seconds": compute_age_seconds,
            "stale_after_minutes": stale_after_minutes,
            "stale_compute_after_minutes": 75,
            "stale_ingest_after_minutes": stale_after_minutes,
            "stale_threshold_seconds": stale_threshold_seconds,
            "stale_reasons": stale_reasons,
            "is_stale": is_stale,
            "timezone": "Europe/London" if UK_TZ_AVAILABLE else "UTC_FALLBACK",
            "last_08_01_candle_time_utc": permission_health.get("candle_time_utc"),
            "daily_permission_target_utc": permission_health.get("target_utc"),
            "daily_permission_target_london": permission_health.get("target_london"),
            "broker_offset_seconds": permission_health.get("broker_offset_seconds"),
            "broker_offset_hours": permission_health.get("broker_offset_hours"),
            "broker_server_time_utc": permission_health.get("broker_server_time_utc"),
            "expected_0801_broker_time": permission_health.get("expected_0801_broker_time"),
            "actual_candle_found_time": permission_health.get("actual_candle_found_time"),
            "daily_permission_missing": bool(permission_health.get("missing")),
            "daily_permission_degraded": bool(permission_health.get("degraded")),
            "daily_permission_degraded_reason": permission_health.get("reason"),
            "runner_last_error": permission_health.get("runner_last_error"),
            "daily_permission_backfill_attempted": bool(permission_health.get("backfill_attempted")),
            "daily_permission_backfill_result": permission_health.get("backfill_result"),
            "permission_stage": out.get("permission_stage") or permission_health.get("permission_stage"),
            "permission_source": out.get("permission_source") or permission_health.get("permission_source"),
            "permission_lock_time_london": out.get("permission_lock_time_london")
            or permission_health.get("permission_lock_time_london"),
            "permission_for_date_uk": out.get("permission_for_date_uk") or permission_health.get("for_date_uk"),
        }
    )
    return out


@router.get("/status")
def get_oracle_status(
    symbol: str | None = None,
    stale_after_minutes: int = 20,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    plan = _resolve_plan(db, user)
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    symbols = selected
    if symbol:
        symbol_value = symbol.strip().upper()
        if symbol_value not in allowed:
            raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not available on your tier")
        if symbol_value not in selected:
            raise HTTPException(status_code=403, detail=f"Symbol '{symbol_value}' is not enabled in your settings")
        symbols = [symbol_value]

    now_utc = datetime.now(timezone.utc)
    items: list[dict] = []
    timeframe_seconds = 15 * 60
    stale_threshold_seconds = 300
    for symbol_value in symbols:
        ingest_row = _latest_ingest_status(db, symbol_value)
        compute_row = _latest_snapshot(db, symbol_value)
        latest_m15 = _latest_candle(db, symbol=symbol_value, timeframe="M15")

        last_ingest = _as_utc(ingest_row.last_ingested_at) if ingest_row else None
        last_compute = _snapshot_compute_time(compute_row)
        last_snapshot_as_of = _as_utc(compute_row.as_of_utc) if compute_row else None
        last_candle_close = _as_utc(latest_m15.time_utc) if latest_m15 else None

        compute_age_seconds = max(int((now_utc - last_compute).total_seconds()), 0) if last_compute else None
        ingest_age_seconds = max(int((now_utc - last_ingest).total_seconds()), 0) if last_ingest else None
        candle_age_seconds = max(int((now_utc - last_candle_close).total_seconds()), 0) if last_candle_close else None
        stale_reasons: list[str] = []
        def _add_reason(reason: str) -> None:
            if reason not in stale_reasons:
                stale_reasons.append(reason)

        if last_ingest is None:
            _add_reason("mt5_down")
        elif ingest_age_seconds is not None and ingest_age_seconds > stale_threshold_seconds:
            _add_reason("ingest_lag")
        if last_candle_close is None:
            _add_reason("m15_missing")

        permission_health = _daily_permission_health(db, symbol_value, now_utc=now_utc)
        is_stale = len(stale_reasons) > 0
        if is_stale:
            logger.warning(
                "oracle status stale symbol=%s reasons=%s ingest_age_seconds=%s compute_age_seconds=%s",
                symbol_value,
                ",".join(stale_reasons),
                ingest_age_seconds,
                compute_age_seconds,
            )

        items.append(
            {
                "symbol": symbol_value,
                "last_ingest_at": last_ingest.isoformat() if last_ingest else None,
                "last_ingest_at_utc": last_ingest.isoformat() if last_ingest else None,
                "last_snapshot_as_of": last_snapshot_as_of.isoformat() if last_snapshot_as_of else None,
                "last_compute_at": last_compute.isoformat() if last_compute else None,
                "last_compute_at_utc": last_compute.isoformat() if last_compute else None,
                "latest_candle_close_utc": last_candle_close.isoformat() if last_candle_close else None,
                "timeframe_seconds": timeframe_seconds,
                "stale_after_minutes": stale_after_minutes,
                "stale_compute_after_minutes": 75,
                "stale_ingest_after_minutes": stale_after_minutes,
                "stale_threshold_seconds": stale_threshold_seconds,
                "compute_age_seconds": compute_age_seconds,
                "ingest_age_seconds": ingest_age_seconds,
                "candle_age_seconds": candle_age_seconds,
                "age_seconds": compute_age_seconds,
                "is_stale": is_stale,
                "stale_reasons": stale_reasons,
                "timezone": "Europe/London" if UK_TZ_AVAILABLE else "UTC_FALLBACK",
                "last_08_01_candle_time_utc": permission_health.get("candle_time_utc"),
                "daily_permission_target_utc": permission_health.get("target_utc"),
                "daily_permission_target_london": permission_health.get("target_london"),
                "broker_offset_seconds": permission_health.get("broker_offset_seconds"),
                "broker_offset_hours": permission_health.get("broker_offset_hours"),
                "broker_server_time_utc": permission_health.get("broker_server_time_utc"),
                "expected_0801_broker_time": permission_health.get("expected_0801_broker_time"),
                "actual_candle_found_time": permission_health.get("actual_candle_found_time"),
                "daily_permission_missing": bool(permission_health.get("missing")),
                "daily_permission_degraded": bool(permission_health.get("degraded")),
                "daily_permission_degraded_reason": permission_health.get("reason"),
                "runner_last_error": permission_health.get("runner_last_error"),
                "daily_permission_backfill_attempted": bool(permission_health.get("backfill_attempted")),
                "daily_permission_backfill_result": permission_health.get("backfill_result"),
                "permission_stage": permission_health.get("permission_stage"),
                "permission_source": permission_health.get("permission_source"),
                "permission_lock_time_london": permission_health.get("permission_lock_time_london"),
                "permission_for_date_uk": permission_health.get("for_date_uk"),
            }
        )

    if symbol:
        return items[0]
    return {
        "ok": True,
        "items": items,
        "stale_after_minutes": stale_after_minutes,
        "stale_compute_after_minutes": 75,
        "stale_ingest_after_minutes": stale_after_minutes,
    }


@router.get("/debug/0801-window")
def debug_0801_window(
    symbol: str = "XAUUSD",
    for_date_uk: date | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not UK_TZ_AVAILABLE:
        raise HTTPException(status_code=503, detail="Europe/London timezone unavailable.")

    symbol_value = symbol.strip().upper()
    if not symbol_value:
        raise HTTPException(status_code=400, detail="symbol is required")

    date_value = for_date_uk or datetime.now(UK_TZ).date()
    return _london_0801_window_debug(db, symbol=symbol_value, for_date_uk=date_value)


@router.post("/run-basic")
def run_basic_oracle(
    symbol: str | None = None,
    timeframe: str | None = None,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    resolved_symbol = symbol or settings.ORACLE_SYMBOL
    resolved_timeframe = timeframe or settings.ORACLE_TIMEFRAME
    run_id = uuid4()

    provider = get_data_provider()
    try:
        candle = provider.get_latest_closed_candle(
            symbol=resolved_symbol,
            timeframe=resolved_timeframe,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch market data: {exc}") from exc

    decision = oracle_from_candle(
        symbol=candle.symbol,
        o=candle.open,
        h=candle.high,
        l=candle.low,
        c=candle.close,
    )

    ms = MarketStateDaily(
        symbol=decision.symbol,
        date_uk=datetime.now(UK_TZ),
        allowed_direction=decision.direction,
        internal_bias_json={
            "engine": "daily_oracle",
            "reason": decision.reason,
            "data_provider": provider.name,
            "timeframe": candle.timeframe,
            "candle_time_utc": candle.time_utc.isoformat(),
            "o": candle.open,
            "h": candle.high,
            "l": candle.low,
            "c": candle.close,
            "volume": candle.volume,
        },
    )
    db.add(ms)

    _upsert_gold_regime_from_oracle(
        db,
        symbol=decision.symbol,
        as_of_utc=candle.time_utc,
        direction=decision.direction,
        reason=decision.reason,
        o=candle.open,
        h=candle.high,
        l=candle.low,
        c=candle.close,
        provider_name=provider.name,
        timeframe=candle.timeframe,
    )
    db.commit()

    recipients = (
        db.query(User, NotificationRoute, UserSignalPref, Subscription)
        .outerjoin(NotificationRoute, NotificationRoute.user_id == User.id)
        .outerjoin(UserSignalPref, UserSignalPref.user_id == User.id)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .all()
    )

    sent = 0
    consumed = 0
    failed = 0
    skipped = 0

    for user, route, pref, sub in recipients:
        tier = (sub.plan if sub else "basic") or "basic"
        sub_status = (sub.status if sub else "missing") or "missing"
        selected_symbols = get_user_enabled_symbols(db, user.id, tier)
        pref_enabled = bool(pref.telegram_enabled) if pref else False
        pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
        route_enabled = bool(route.telegram_enabled) if route else False
        route_chat = (route.telegram_chat_id or "").strip() if route else ""
        effective_enabled = pref_enabled or route_enabled
        effective_chat_id = pref_chat or route_chat

        context = {
            "symbol": decision.symbol,
            "timeframe": candle.timeframe,
            "candle_time_utc": candle.time_utc.isoformat(),
            "allowed_direction": decision.direction,
        }

        if not user.is_active:
            skipped += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail="User inactive",
                    context_json=context,
                )
            )
            continue

        if not effective_enabled or not effective_chat_id:
            skipped += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail="telegram_not_connected",
                    context_json=context,
                )
            )
            continue

        if decision.symbol not in selected_symbols:
            skipped += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail="symbol_not_enabled",
                    context_json=context,
                )
            )
            continue

        plan_normalized = normalize_plan(tier)
        try:
            validate_symbol_for_strategy(
                symbol=decision.symbol,
                strategy_name=DAILY_BIAS,
                tier=plan_normalized,
            )
        except StrategyMatrixError as exc:
            skipped += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail=f"strategy_matrix_blocked:{exc.reason}",
                    context_json={
                        **context,
                        "strategy_name": DAILY_BIAS,
                        "matrix_reason": exc.reason,
                    },
                )
            )
            log_audit(
                db,
                action="signal.send.skipped_strategy_matrix",
                user_id=user.id,
                meta={
                    "source": "oracle_run",
                    "symbol": decision.symbol,
                    "tier": plan_normalized,
                    "strategy_name": DAILY_BIAS,
                    "matrix_reason": exc.reason,
                },
            )
            continue

        usage_pre = None
        if getattr(user, "role", "user") != "admin":
            usage_pre = get_usage(db, user.id)
            if usage_pre["limit"] is not None and int(usage_pre["remaining"] or 0) <= 0:
                skipped += 1
                db.add(
                    DeliveryLog(
                        run_id=run_id,
                        user_id=user.id,
                        symbol=decision.symbol,
                        source="oracle_run",
                        tier=tier,
                        subscription_status=sub_status,
                        send_status="SKIPPED",
                        consume_status="NOT_ATTEMPTED",
                        detail="usage_limit_exceeded",
                        context_json={**context, "usage": usage_pre},
                    )
                )
                log_audit(
                    db,
                    action="signal.send.skipped_usage_limit",
                    user_id=user.id,
                    meta={
                        "source": "oracle_run",
                        "symbol": decision.symbol,
                        "tier": tier,
                        "used": usage_pre["used"],
                        "limit": usage_pre["limit"],
                        "resets_at": usage_pre["resets_at"],
                    },
                )
                continue

        try:
            pack = get_gold_today_pack(
                symbol=decision.symbol,
                consume=False,
                user=user,
                db=db,
            )
        except HTTPException as exc:
            skipped += 1
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status="SKIPPED",
                    consume_status="NOT_ATTEMPTED",
                    detail=_http_detail(exc),
                    context_json=context,
                )
            )
            continue

        context["headline"] = pack.get("headline")
        text = _format_user_outcome_message(pack)

        send_status = "SENT"
        consume_status = "NOT_ATTEMPTED"
        detail: str | None = None

        try:
            send_telegram_message(effective_chat_id, text)
            sent += 1
        except Exception as exc:
            failed += 1
            send_status = "FAILED"
            detail = str(exc)
            db.add(
                DeliveryLog(
                    run_id=run_id,
                    user_id=user.id,
                    symbol=decision.symbol,
                    source="oracle_run",
                    tier=tier,
                    subscription_status=sub_status,
                    send_status=send_status,
                    consume_status=consume_status,
                    detail=detail,
                    context_json=context,
                )
            )
            continue

        if getattr(user, "role", "user") != "admin":
            try:
                usage_after = consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="oracle_run",
                    symbol=decision.symbol,
                    signal_id=f"oracle_run:{run_id}",
                    meta={"symbol": decision.symbol, "tier": tier},
                )
                consume_status = "CONSUMED"
                consumed += 1
                context["usage"] = usage_after
            except UsageLimitExceeded as exc:
                consume_status = "FAILED"
                detail = "usage_limit_exceeded_post_send"
                context["usage_error"] = exc.payload
                log_audit(
                    db,
                    action="signal.send.usage_limit_post_send",
                    user_id=user.id,
                    meta={
                        "source": "oracle_run",
                        "symbol": decision.symbol,
                        "tier": tier,
                        "payload": exc.payload,
                    },
                )
        else:
            consume_status = "NOT_ATTEMPTED"

        db.add(
            DeliveryLog(
                run_id=run_id,
                user_id=user.id,
                symbol=decision.symbol,
                source="oracle_run",
                tier=tier,
                subscription_status=sub_status,
                send_status=send_status,
                consume_status=consume_status,
                detail=detail,
                context_json=context,
            )
        )

    db.commit()

    return {
        "ok": True,
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "candle_time_utc": candle.time_utc.isoformat(),
        "direction": decision.direction,
        "source": provider.name,
        "sent": sent,
        "consumed": consumed,
        "failed": failed,
        "skipped": skipped,
    }
