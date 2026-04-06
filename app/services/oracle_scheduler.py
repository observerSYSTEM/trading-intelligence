from __future__ import annotations

import logging
import hashlib
import threading
import uuid
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.symbols import enabled_symbols_from_settings, normalize_plan
from app.core.time_utils import LONDON_TZ, LONDON_TZ_AVAILABLE, london_now, now_utc
from app.db.models import (
    DailyPermissionSnapshot,
    DeliveryLog,
    GoldRegimeDaily,
    LiquiditySignal,
    MT5Candle,
    MT5IngestStatus,
    NotificationRoute,
    OracleMagnetState,
    OracleTargetsSnapshot,
    OracleConfirmation,
    OraclePermissionDaily,
    OracleProcessingState,
    OracleQuarterlySnapshot,
    OracleRun,
    SignalDelivery,
    Subscription,
    RunnerStatus,
    TradeEvent,
    User,
    UserSignalPref,
    WeeklyRangeSnapshot,
)
from app.db.session import SessionLocal
from app.schemas.signal import SignalCreate
from app.services.data_provider import get_data_provider
from app.services.oracle_engine import (
    OpportunityResult,
    compute_prelim_permission_from_asia,
    compute_daily_permission_from_m1,
    compute_hourly_candidate,
    compute_opportunity_with_h1_confirmation,
    compute_permission_decision,
    compute_quarterly_snapshot,
    compute_weekly_range_snapshot,
    confirm_with_m15,
)
from app.services.audit import log_audit
from app.services.autotrade_service import queue_autotrade_job_for_user
from app.services.strategy_matrix import (
    DAILY_BIAS,
    ZONE_TO_ZONE,
    StrategyMatrixError,
    validate_symbol_for_strategy,
)
from app.services.telegram_service import send_message as send_telegram_message, send_thread_update
from app.services.symbol_preferences import get_user_enabled_symbols
from app.services.signal_publisher import publish_signal
from app.services.signal_service import find_refreshable_signal, signal_payload_requires_refresh
from app.services.telegram_alerts import (
    build_risk_stale_warning_message,
    latest_oracle_alert_context,
    maybe_send_daily_alignment_alert,
    maybe_send_liquidity_target_alert,
    maybe_send_m15_opportunity_confirmed_alert,
)
from app.services.trade_tracker import (
    build_daily_audit_message,
    create_trade_for_signal,
    format_london,
    monitor_open_trades,
    to_uk_date,
)
from app.services.trade_validation import validate_trade_payload
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage
from app.services.targets_refresh import (
    backfill_london_open_m1_window,
    ingest_latest_candles,
    latest_magnet_state,
    maybe_refresh_targets_on_magnet_hit,
    refresh_targets_for_all_symbols,
)

logger = logging.getLogger(__name__)

ACTIVE_SUB_STATUSES = {"active", "trialing"}
TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}
NON_CRITICAL_UPDATE_SOURCES = {"magnet_update", "opportunity", "oracle_bias"}

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
UK_TZ: timezone | ZoneInfo = LONDON_TZ
UK_TZ_AVAILABLE = LONDON_TZ_AVAILABLE


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _permission_lock_time_london(for_date) -> str | None:
    try:
        value = datetime(for_date.year, for_date.month, for_date.day, 8, 1, tzinfo=UK_TZ)
        return value.isoformat()
    except Exception:
        return None


def _magnet_threshold_for_symbol(symbol: str) -> float:
    sym = (symbol or "").strip().upper()
    if sym == "XAUUSD":
        return 0.30
    if sym == "BTCUSD":
        return 30.0
    if sym == "GBPJPY":
        return 0.08
    if sym in {"GBPUSD", "EURUSD"}:
        return 0.0005
    return 0.05


def _magnet_changed_significantly(symbol: str, old_price: float | None, new_price: float | None) -> bool:
    if old_price is None and new_price is None:
        return False
    if old_price is None or new_price is None:
        return True
    return abs(float(new_price) - float(old_price)) >= _magnet_threshold_for_symbol(symbol)


def _latest_magnet_snapshot_pair(db, *, symbol: str, tier: str = "pro"):
    from app.db.models import OracleTargetsSnapshot

    rows = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == tier)
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .limit(2)
        .all()
    )
    if len(rows) < 2:
        return None, None
    return rows[1], rows[0]


def _magnet_row_changed(
    symbol: str,
    *,
    before: OracleMagnetState | None,
    after: OracleMagnetState | None,
) -> bool:
    if before is None:
        return after is not None
    if after is None:
        return False
    if str(before.magnet_side or "").upper() != str(after.magnet_side or "").upper():
        return True
    return _magnet_changed_significantly(symbol, before.magnet_price, after.magnet_price)


def _normalized_opportunity_signature_from_run(run: OracleRun | None) -> dict | None:
    if run is None:
        return None
    public = run.public_json if isinstance(run.public_json, dict) else {}

    def _num(value):
        if value is None:
            return None
        try:
            return round(float(value), 5)
        except Exception:
            return None

    return {
        "daily_permission": str(public.get("daily_permission") or "").upper(),
        "permission_stage": str(public.get("permission_stage") or "").upper(),
        "permission_source": str(public.get("permission_source") or "").upper(),
        "opportunity_direction": str(public.get("opportunity_direction") or "").upper(),
        "final_allowed": str(public.get("final_allowed_basic") or "").upper(),
        "confirm_ok": bool(public.get("confirm_ok")),
        "m15_close": _num(public.get("m15_close")),
        "h1_close": _num(public.get("h1_close")),
    }


def _normalized_opportunity_signature_from_opp(
    *,
    permission: str,
    permission_stage: str | None,
    permission_source: str | None,
    opp: OpportunityResult,
) -> dict:
    public = opp.public_json if isinstance(opp.public_json, dict) else {}

    def _num(value):
        if value is None:
            return None
        try:
            return round(float(value), 5)
        except Exception:
            return None

    return {
        "daily_permission": str(permission or "").upper(),
        "permission_stage": str(permission_stage or "").upper(),
        "permission_source": str(permission_source or "").upper(),
        "opportunity_direction": str(opp.opportunity_direction or "").upper(),
        "final_allowed": str(opp.final_allowed or "").upper(),
        "confirm_ok": bool(opp.h1_confirm_ok),
        "m15_close": _num(public.get("m15_close")),
        "h1_close": _num(public.get("h1_close")),
    }


def _ensure_daily_permission_snapshot(db, *, symbol: str, ref_utc: datetime | None = None):
    now_value = _as_utc(ref_utc or now_utc())
    if not UK_TZ_AVAILABLE:
        fallback = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=now_value)
        row = _upsert_daily_permission_snapshot(db, result=fallback)
        return row, None

    local_now = now_value.astimezone(UK_TZ)
    if (local_now.hour, local_now.minute) < (8, 2):
        prelim = compute_prelim_permission_from_asia(db, symbol=symbol, ref_utc=now_value)
        row = _upsert_daily_permission_snapshot(db, result=prelim)
        return row, None

    first = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=now_value)
    backfill_result: dict | None = None
    factors = first.factors_json if isinstance(first.factors_json, dict) else {}
    missing = bool(factors.get("missing_data"))
    if missing:
        runner_row = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()
        if runner_row is not None and not bool(runner_row.mt5_connected):
            runner_error = (runner_row.last_error or "Runner MT5 disconnected.").strip()
            backfill_result = {
                "ok": False,
                "skipped": True,
                "reason": "runner_mt5_disconnected",
                "runner_last_error": runner_error,
            }
            factors["backfill_attempted"] = False
            factors["backfill_result"] = backfill_result
            factors["runner_last_error"] = runner_error
            first.factors_json = factors
            first.reason = f"08:01 candle missing; runner MT5 is disconnected. {runner_error}"
        else:
            backfill_result = backfill_london_open_m1_window(db, symbol=symbol, date_uk=first.date_uk)
            if backfill_result.get("ok"):
                second = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=now_value)
                factors2 = second.factors_json if isinstance(second.factors_json, dict) else {}
                factors2["backfill_attempted"] = True
                factors2["backfill_result"] = backfill_result
                second.factors_json = factors2
                first = second
            else:
                factors["backfill_attempted"] = True
                factors["backfill_result"] = backfill_result
                first.factors_json = factors

    row = _upsert_daily_permission_snapshot(db, result=first)
    return row, backfill_result


def _regime_from_direction(direction: str) -> str:
    if direction == "BUY_ONLY":
        return "bullish"
    if direction == "SELL_ONLY":
        return "bearish"
    return "range"


def _selected_symbols_for_user(db, user_id, plan: str) -> list[str]:
    return get_user_enabled_symbols(db, user_id, plan)


def _record_trade_audit_event(
    db,
    *,
    user_id,
    symbol: str,
    event_type: str,
    tier_min: str,
    title: str,
    message: str,
    meta_json: dict | None = None,
) -> None:
    db.add(
        TradeEvent(
            trade_id=None,
            user_id=user_id,
            symbol=symbol,
            event_type=event_type,
            tier_min=tier_min,
            title=title[:160],
            message=message[:4000],
            meta_json=meta_json or {},
            note=None,
            price=None,
        )
    )


def _event_type_for_source(source: str) -> str:
    if source in {"oracle_bias", "admin_broadcast"}:
        return "SIGNAL"
    if source == "trade_entry":
        return "ENTRY"
    if source == "trade_monitor":
        return "UPDATE"
    if source == "daily_audit":
        return "DAILY_AUDIT"
    return "BIAS"


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _message_content_hash(*, source: str, symbol: str, title: str, body: str) -> str:
    normalized = (
        f"{(source or '').strip().lower()}|"
        f"{(symbol or '').strip().upper()}|"
        f"{(title or '').strip()}|"
        f"{(body or '').strip()}"
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _safe_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _latest_liquidity_context(db, *, symbol: str) -> dict:
    symbol_value = (symbol or "").strip().upper()
    context = {
        "magnet_level": None,
        "magnet_side": None,
        "sellside_liquidity": None,
        "buyside_liquidity": None,
        "zone_to_zone_target": None,
    }

    snapshot = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol_value, OracleTargetsSnapshot.tier == "pro")
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .first()
    )
    if snapshot is None:
        snapshot = (
            db.query(OracleTargetsSnapshot)
            .filter(OracleTargetsSnapshot.symbol == symbol_value)
            .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
            .first()
        )

    if snapshot is not None:
        context["magnet_level"] = _safe_float(snapshot.magnet_price)
        context["sellside_liquidity"] = _safe_float(snapshot.sellside_liquidity)
        context["buyside_liquidity"] = _safe_float(snapshot.buyside_liquidity)
        context["zone_to_zone_target"] = _safe_float(snapshot.zone_to_zone_target)
        state = snapshot.magnet_state if isinstance(snapshot.magnet_state, dict) else {}
        current = state.get("current") if isinstance(state.get("current"), dict) else {}
        if isinstance(current.get("side"), str):
            context["magnet_side"] = str(current.get("side")).upper()
        elif isinstance(state.get("magnet_side"), str):
            context["magnet_side"] = str(state.get("magnet_side")).upper()

    magnet_row = latest_magnet_state(db, symbol=symbol_value)
    if magnet_row is not None:
        if context["magnet_level"] is None:
            context["magnet_level"] = _safe_float(magnet_row.magnet_price)
        if context["magnet_side"] is None:
            context["magnet_side"] = str(magnet_row.magnet_side or "").upper() or None
        if context["sellside_liquidity"] is None:
            context["sellside_liquidity"] = _safe_float(magnet_row.sellside_liquidity)
        if context["buyside_liquidity"] is None:
            context["buyside_liquidity"] = _safe_float(magnet_row.buyside_liquidity)
        if context["zone_to_zone_target"] is None:
            context["zone_to_zone_target"] = _safe_float(magnet_row.zone_to_zone_target)

    return context


def _preferred_magnet_for_bias(liquidity_ctx: dict, *, bias: str) -> float | None:
    bias_value = str(bias or "").upper()
    if bias_value == "SELL_ONLY":
        return _safe_float(liquidity_ctx.get("sellside_liquidity")) or _safe_float(liquidity_ctx.get("magnet_level"))
    if bias_value == "BUY_ONLY":
        return _safe_float(liquidity_ctx.get("buyside_liquidity")) or _safe_float(liquidity_ctx.get("magnet_level"))
    return _safe_float(liquidity_ctx.get("magnet_level"))


def _build_active_setup_key(
    *,
    symbol: str,
    timeframe: str,
    signal_type: str,
    bias: str,
    daily_permission: str,
    h1_confirmation: str,
    detected_at: datetime,
) -> str:
    detected_date_uk = _as_utc(detected_at).astimezone(UK_TZ).date().isoformat()
    return "|".join(
        [
            symbol.strip().upper(),
            timeframe.strip().upper(),
            signal_type.strip().lower(),
            bias.strip().upper(),
            daily_permission.strip().upper(),
            h1_confirmation.strip().upper(),
            detected_date_uk,
        ]
    )


def _build_opportunity_signal_payload(
    *,
    db,
    symbol: str,
    permission: str,
    permission_stage: str | None,
    permission_source: str | None,
    opp: OpportunityResult,
) -> SignalCreate:
    public = opp.public_json if isinstance(opp.public_json, dict) else {}
    liquidity_ctx = _latest_liquidity_context(db, symbol=symbol)
    symbol_value = (symbol or "").strip().upper()
    timeframe_value = str(opp.timeframe_signal or "M15").upper()
    final_allowed = str(opp.final_allowed or "").upper()
    permission_value = (
        str(permission).upper()
        if str(permission).upper() in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"}
        else "NO_TRADE"
    )
    opportunity_direction = str(opp.opportunity_direction or "").upper() or "NO_TRADE"
    confidence_value = round(float(opp.confidence), 4)
    price_value = _safe_float(public.get("m15_close")) or _safe_float(public.get("h1_close"))
    magnet_side = str(liquidity_ctx.get("magnet_side") or "").upper() or None
    zone_target = _safe_float(liquidity_ctx.get("zone_to_zone_target"))
    general_magnet = _safe_float(liquidity_ctx.get("magnet_level"))
    h1_confirmation = "CONFIRMED" if bool(opp.h1_confirm_ok) else "NOT_CONFIRMED"
    daily_alignment = "ALIGNED" if bool(opp.aligned) and permission_value in {"BUY_ONLY", "SELL_ONLY"} else "CONFLICT"
    reason_value = str(opp.reason or "")[:240]
    reason_short = str(opp.reason or "").strip()
    if len(reason_short) > 160:
        reason_short = reason_short[:157].rstrip() + "..."

    if final_allowed in {"BUY_ONLY", "SELL_ONLY"}:
        signal_type = "opportunity_m15_confirmed"
        selected_magnet = _preferred_magnet_for_bias(liquidity_ctx, bias=final_allowed)
        return SignalCreate(
            symbol=symbol_value,
            timeframe=timeframe_value,
            signal_type=signal_type,
            direction=final_allowed,
            magnet=selected_magnet,
            magnet_level=selected_magnet,
            price=price_value,
            bias=final_allowed,
            reason=reason_value,
            confidence=confidence_value,
            daily_permission=permission_value,
            h1_confirmation=h1_confirmation,
            zone_target=zone_target,
            sellside_liquidity=_safe_float(liquidity_ctx.get("sellside_liquidity")),
            buyside_liquidity=_safe_float(liquidity_ctx.get("buyside_liquidity")),
            source="oracle_engine",
            detected_at=_as_utc(opp.as_of_utc),
            meta={
                "symbol_display": symbol_value,
                "timeframe_display": timeframe_value,
                "magnet_side": magnet_side,
                "magnet_snapshot": general_magnet,
                "sellside_liquidity": _safe_float(liquidity_ctx.get("sellside_liquidity")),
                "buyside_liquidity": _safe_float(liquidity_ctx.get("buyside_liquidity")),
                "zone_to_zone_target": zone_target,
                "zone_target": zone_target,
                "h1_confirm_ok": bool(opp.h1_confirm_ok),
                "h1_confirmation": h1_confirmation,
                "daily_permission": permission_value,
                "daily_alignment": daily_alignment,
                "permission_stage": str(permission_stage or "").upper() or None,
                "permission_source": str(permission_source or "").upper() or None,
                "opportunity_direction": opportunity_direction,
                "confidence": confidence_value,
                "reason": reason_value,
                "reason_short": reason_short,
                "final_allowed": final_allowed,
                "active_setup_key": _build_active_setup_key(
                    symbol=symbol_value,
                    timeframe=timeframe_value,
                    signal_type=signal_type,
                    bias=final_allowed,
                    daily_permission=permission_value,
                    h1_confirmation=h1_confirmation,
                    detected_at=_as_utc(opp.as_of_utc),
                ),
            },
        )

    detected_at_utc = _as_utc(opp.as_of_utc)
    direction_value = "NO_TRADE"
    if magnet_side == "BUY":
        direction_value = "BUY_ONLY"
    elif magnet_side == "SELL":
        direction_value = "SELL_ONLY"

    snapshot_reason = "No actionable opportunity; publishing current liquidity magnet context for Pro visibility."
    snapshot_reason_short = "No actionable setup; liquidity magnet context snapshot."
    signal_type = "magnet_snapshot"
    return SignalCreate(
        symbol=symbol_value,
        timeframe=timeframe_value,
        signal_type=signal_type,
        direction=direction_value,
        magnet=general_magnet,
        magnet_level=general_magnet,
        price=price_value,
        bias=permission_value,
        reason=snapshot_reason,
        confidence=confidence_value,
        daily_permission=permission_value,
        h1_confirmation=h1_confirmation,
        zone_target=zone_target,
        sellside_liquidity=_safe_float(liquidity_ctx.get("sellside_liquidity")),
        buyside_liquidity=_safe_float(liquidity_ctx.get("buyside_liquidity")),
        source="oracle_engine",
        detected_at=detected_at_utc,
        meta={
            "symbol_display": symbol_value,
            "timeframe_display": timeframe_value,
            "magnet_side": magnet_side,
            "magnet_price": general_magnet,
            "sellside_liquidity": _safe_float(liquidity_ctx.get("sellside_liquidity")),
            "buyside_liquidity": _safe_float(liquidity_ctx.get("buyside_liquidity")),
            "zone_to_zone_target": zone_target,
            "zone_target": zone_target,
            "daily_permission": permission_value,
            "daily_alignment": daily_alignment,
            "permission_stage": str(permission_stage or "").upper() or None,
            "permission_source": str(permission_source or "").upper() or None,
            "confidence": confidence_value,
            "reason": snapshot_reason,
            "reason_short": snapshot_reason_short,
            "snapshot_only": True,
            "opportunity_direction": opportunity_direction,
            "final_allowed": final_allowed or "NO_TRADE",
            "active_setup_key": _build_active_setup_key(
                symbol=symbol_value,
                timeframe=timeframe_value,
                signal_type=signal_type,
                bias=direction_value or permission_value,
                daily_permission=permission_value,
                h1_confirmation=h1_confirmation,
                detected_at=detected_at_utc,
            ),
        },
    )


def _publish_opportunity_signal_to_ingest(
    *,
    db,
    symbol: str,
    permission: str,
    permission_stage: str | None,
    permission_source: str | None,
    opp: OpportunityResult,
) -> dict:
    payload = _build_opportunity_signal_payload(
        db=db,
        symbol=symbol,
        permission=permission,
        permission_stage=permission_stage,
        permission_source=permission_source,
        opp=opp,
    )
    refreshable = find_refreshable_signal(db, payload=payload)
    refresh_needed = refreshable is None or signal_payload_requires_refresh(refreshable, payload=payload)
    payload_dump = payload.model_dump(mode="json")
    if not refresh_needed:
        return {
            "ok": True,
            "skipped": True,
            "reason": "signal_unchanged",
            "refresh_needed": False,
            "payload": payload_dump,
        }

    result = publish_signal(payload)
    result["refresh_needed"] = True
    result["payload"] = payload_dump
    return result


def _mark_runner_telegram_sent(db, *, sent_at: datetime) -> None:
    status = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()
    if status is None:
        return
    status.last_telegram_sent_utc = _as_utc(sent_at)
    db.add(status)
    db.commit()


def _maybe_send_aligned_signal_alert(
    db,
    *,
    signal_payload: dict | None,
    material_refresh: bool,
) -> dict:
    if not isinstance(signal_payload, dict):
        return {"status": "not_applicable", "reason": "missing_signal_payload"}

    meta = signal_payload.get("meta") if isinstance(signal_payload.get("meta"), dict) else {}
    signal_type = str(signal_payload.get("signal_type") or "").strip().lower()
    symbol = str(signal_payload.get("symbol") or "").strip().upper()
    timeframe = str(signal_payload.get("timeframe") or "").strip().upper()
    bias = str(signal_payload.get("bias") or signal_payload.get("direction") or "").strip().upper()
    daily_permission = str(signal_payload.get("daily_permission") or bias).strip().upper()
    daily_alignment = str(meta.get("daily_alignment") or "").strip().upper()
    h1_confirmation = str(signal_payload.get("h1_confirmation") or meta.get("h1_confirmation") or "").strip().upper()
    permission_stage = str(meta.get("permission_stage") or "").strip().upper()
    permission_source = str(meta.get("permission_source") or "").strip().upper()
    final_allowed = str(meta.get("final_allowed") or signal_payload.get("direction") or bias).strip().upper()
    m15_opportunity = str(meta.get("opportunity_direction") or signal_payload.get("direction") or bias).strip().upper()
    magnet = _safe_float(signal_payload.get("magnet")) or _safe_float(signal_payload.get("magnet_level"))
    zone_target = _safe_float(signal_payload.get("zone_target")) or _safe_float(meta.get("zone_to_zone_target"))
    sellside = _safe_float(signal_payload.get("sellside_liquidity")) or _safe_float(meta.get("sellside_liquidity"))
    buyside = _safe_float(signal_payload.get("buyside_liquidity")) or _safe_float(meta.get("buyside_liquidity"))
    reason = str(signal_payload.get("reason") or meta.get("reason") or "").strip()
    confidence = _safe_float(signal_payload.get("confidence")) or _safe_float(meta.get("confidence")) or 0.0
    detected_raw = str(signal_payload.get("detected_at") or "")
    try:
        detected_at = _as_utc(datetime.fromisoformat(detected_raw))
    except ValueError:
        detected_at = now_utc()

    if not (
        symbol
        and timeframe == "M15"
        and bias in {"BUY_ONLY", "SELL_ONLY"}
        and daily_alignment == "ALIGNED"
        and signal_type == "opportunity_m15_confirmed"
    ):
        return {"status": "not_applicable", "reason": "alignment_incomplete"}

    alert_result = maybe_send_m15_opportunity_confirmed_alert(
        symbol=symbol,
        detected_at=detected_at,
        permission_source=permission_source,
        permission_stage=permission_stage,
        daily_permission=daily_permission,
        final_allowed=final_allowed,
        h1_confirmation=h1_confirmation,
        m15_opportunity=m15_opportunity,
        confidence=confidence,
        reason=reason,
        magnet=magnet,
        zone_target=zone_target,
        sellside=sellside,
        buyside=buyside,
        active_setup_key=str(meta.get("active_setup_key") or "").strip() or None,
        material_refresh=material_refresh,
    )
    if alert_result.get("status") == "alert_sent":
        try:
            _mark_runner_telegram_sent(db, sent_at=detected_at)
        except Exception:
            db.rollback()
            logger.exception("alert_failed runner_status_update symbol=%s timeframe=%s", symbol, timeframe)
    return alert_result


def _sent_content_hash_exists(
    db,
    *,
    user_id,
    symbol: str,
    source: str,
    content_hash: str,
) -> bool:
    recent_rows = (
        db.query(DeliveryLog)
        .filter(DeliveryLog.user_id == user_id)
        .filter(DeliveryLog.symbol == symbol)
        .filter(DeliveryLog.source == source)
        .filter(DeliveryLog.send_status == "SENT")
        .order_by(DeliveryLog.created_at.desc())
        .limit(20)
        .all()
    )
    for row in recent_rows:
        ctx = row.context_json if isinstance(row.context_json, dict) else {}
        if str(ctx.get("content_hash") or "") == content_hash:
            return True
    return False


def _upsert_oracle_processing_state(
    db,
    *,
    symbol: str,
    timeframe: str,
    candle_time_utc: datetime,
) -> None:
    row = (
        db.query(OracleProcessingState)
        .filter(
            OracleProcessingState.symbol == symbol,
            OracleProcessingState.timeframe == timeframe,
        )
        .first()
    )
    candle_time = _as_utc(candle_time_utc)
    if row is None:
        row = OracleProcessingState(
            symbol=symbol,
            timeframe=timeframe,
            last_processed_candle_utc=candle_time,
            last_compute_at_utc=now_utc(),
        )
        db.add(row)
        return
    row.last_processed_candle_utc = candle_time
    row.last_compute_at_utc = now_utc()
    db.add(row)


def _uk_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    local = _as_utc(now_utc).astimezone(UK_TZ)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _should_throttle_conflict_update(db, *, user_id, symbol: str, plan: str, permission_alignment: str) -> bool:
    if plan not in {"pro", "elite"} or permission_alignment != "CONFLICT":
        return False
    start_utc, end_utc = _uk_day_bounds_utc(datetime.now(timezone.utc))
    sent_today = (
        db.query(func.count(DeliveryLog.id))
        .filter(DeliveryLog.user_id == user_id)
        .filter(DeliveryLog.symbol == symbol)
        .filter(DeliveryLog.source == "oracle_bias")
        .filter(DeliveryLog.send_status == "SENT")
        .filter(DeliveryLog.created_at >= start_utc)
        .filter(DeliveryLog.created_at <= end_utc)
        .scalar()
    )
    return int(sent_today or 0) >= 1


def _record_delivery_log(
    db,
    *,
    run_id: UUID,
    user_id,
    symbol: str,
    source: str,
    tier: str,
    subscription_status: str | None,
    send_status: str,
    detail: str | None = None,
    context_json: dict | None = None,
) -> None:
    db.add(
        DeliveryLog(
            run_id=run_id,
            user_id=user_id,
            symbol=symbol,
            source=source,
            tier=tier,
            subscription_status=subscription_status,
            send_status=send_status,
            consume_status="CONSUMED" if send_status == "SENT" else "NOT_ATTEMPTED",
            detail=detail,
            context_json=context_json or {},
        )
    )


def _non_critical_rate_limited(db, *, user_id, symbol: str, source: str) -> bool:
    if source not in NON_CRITICAL_UPDATE_SOURCES:
        return False
    since = datetime.now(timezone.utc) - timedelta(minutes=2)
    recent = (
        db.query(DeliveryLog.id)
        .filter(DeliveryLog.user_id == user_id)
        .filter(DeliveryLog.symbol == symbol)
        .filter(DeliveryLog.send_status == "SENT")
        .filter(DeliveryLog.source.in_(list(NON_CRITICAL_UPDATE_SOURCES)))
        .filter(DeliveryLog.created_at >= since)
        .first()
    )
    return recent is not None


def _schedule_confirm_job(run_id: UUID, run_at_utc: datetime, *, replace_existing: bool = True) -> None:
    global _scheduler
    if _scheduler is None:
        raise RuntimeError("Scheduler not started")
    job_id = f"oracle_confirm_{run_id}"
    _scheduler.add_job(
        run_oracle_confirm_job,
        trigger=DateTrigger(run_date=_as_utc(run_at_utc), timezone=timezone.utc),
        id=job_id,
        replace_existing=replace_existing,
        kwargs={"run_id": str(run_id)},
    )
    logger.info("Scheduled confirmation run_id=%s at=%s", run_id, _as_utc(run_at_utc).isoformat())


def _upsert_gold_regime_snapshot(db, run: OracleRun) -> None:
    public = run.public_json if isinstance(run.public_json, dict) else {}
    internal = run.internal_json if isinstance(run.internal_json, dict) else {}
    row = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == run.symbol, GoldRegimeDaily.as_of_utc == run.as_of_utc)
        .first()
    )
    if not row:
        row = GoldRegimeDaily(symbol=run.symbol, as_of_utc=run.as_of_utc)
        db.add(row)

    final_basic = public.get("final_allowed_basic", run.bias)
    final_elite = public.get("final_allowed_elite", run.bias)
    row.regime = _regime_from_direction(final_basic)
    row.allowed_direction = final_basic
    row.final_allowed_basic = final_basic
    row.final_allowed_elite = final_elite
    row.daily_bias = public.get("daily_bias_raw") or public.get("daily_bias")
    row.confirm_ok = bool(public.get("confirm_ok"))
    row.confidence = run.confidence
    row.notes = public.get("c1") or "Oracle snapshot updated."
    row.public_factors_json = public
    row.internal_factors_json = internal
    db.flush()


def _upsert_quarterly_snapshot(db, *, snapshot) -> None:
    row = (
        db.query(OracleQuarterlySnapshot)
        .filter(
            OracleQuarterlySnapshot.symbol == snapshot.symbol,
            OracleQuarterlySnapshot.quarter_key == snapshot.quarter_key,
        )
        .first()
    )
    if not row:
        row = OracleQuarterlySnapshot(symbol=snapshot.symbol, quarter_key=snapshot.quarter_key)
        db.add(row)

    row.quarter_open = snapshot.quarter_open
    row.q_high = snapshot.q_high_to_date
    row.q_low = snapshot.q_low_to_date
    row.q_mid = snapshot.q_mid_to_date
    row.premium_discount = snapshot.premium_discount
    row.quarterly_bias = snapshot.quarterly_bias
    row.permission_mode = snapshot.permission_mode
    row.conflict_rule = snapshot.conflict_rule
    row.confidence = snapshot.confidence
    row.factors_json = snapshot.factors
    row.as_of_utc = snapshot.as_of_utc
    db.flush()


def _upsert_permission_daily(db, *, decision) -> None:
    row = (
        db.query(OraclePermissionDaily)
        .filter(
            OraclePermissionDaily.symbol == decision.symbol,
            OraclePermissionDaily.date_uk == decision.date_uk,
        )
        .first()
    )
    if not row:
        row = OraclePermissionDaily(symbol=decision.symbol, date_uk=decision.date_uk)
        db.add(row)

    row.daily_bias_raw = decision.daily_bias_raw
    row.quarterly_bias = decision.quarterly_bias
    row.allowed_direction_final = decision.allowed_direction_final
    row.alignment = decision.alignment
    row.confidence_final = decision.confidence_final
    row.message_tag = decision.message_tag
    row.details_json = decision.details
    row.as_of_utc = decision.as_of_utc
    db.flush()


def _upsert_weekly_range_snapshot(db, *, snapshot) -> None:
    row = (
        db.query(WeeklyRangeSnapshot)
        .filter(
            WeeklyRangeSnapshot.symbol == snapshot.symbol,
            WeeklyRangeSnapshot.week_key == snapshot.week_key,
        )
        .first()
    )
    if not row:
        row = WeeklyRangeSnapshot(symbol=snapshot.symbol, week_key=snapshot.week_key)
        db.add(row)

    row.week_start_uk = snapshot.week_start_uk
    row.high = snapshot.high
    row.low = snapshot.low
    row.mid = snapshot.mid
    row.range_ready = snapshot.range_ready
    row.as_of_utc = snapshot.as_of_utc
    row.meta_json = snapshot.meta_json
    db.flush()


def _compute_and_store_permission_state(
    db,
    *,
    symbol: str,
    daily_bias_raw: str,
    daily_confidence: float,
    as_of_utc: datetime | None = None,
):
    snapshot = compute_quarterly_snapshot(db, symbol=symbol, as_of_utc=as_of_utc)
    decision = compute_permission_decision(
        db,
        symbol=symbol,
        daily_bias_raw=daily_bias_raw,
        daily_confidence=daily_confidence,
        as_of_utc=as_of_utc or snapshot.as_of_utc,
        quarterly_snapshot=snapshot,
    )
    _upsert_quarterly_snapshot(db, snapshot=snapshot)
    _upsert_permission_daily(db, decision=decision)
    return snapshot, decision


def _message_template_for_tier(
    *,
    tier: str,
    run: OracleRun,
    time_london: str,
    manipulation_level: str,
    manipulation_reasons: list[str],
) -> tuple[str, bool]:
    public = run.public_json if isinstance(run.public_json, dict) else {}
    symbol = str(run.symbol or "XAUUSD").upper()
    permission_strict = str(public.get("allowed_direction_final_strict", public.get("final_allowed_basic", run.bias)))
    permission_soft = str(public.get("allowed_direction_final_soft", public.get("final_allowed_elite", run.bias)))
    if tier == "basic":
        bias = permission_strict
    else:
        bias = permission_soft
    confidence_pct = int(round(float(run.confidence) * 100))
    timeframe_main = str(public.get("signal_timeframe") or "M15")
    timeframe_fast = "M1"
    fast_bias = str(public.get("daily_permission") or public.get("daily_bias_raw") or bias)

    c1 = public.get("c1", "Directional structure is active.")
    c2 = public.get("c2", "Confirmation remains aligned.")
    l1 = public.get("l1", "Primary liquidity level identified.")
    l2 = public.get("l2", "Secondary liquidity level identified.")
    target = public.get("target", "-")
    reaction = public.get("reaction", "-")
    m1 = public.get("m1", "Trend and structure are aligned.")
    m2 = public.get("m2", "Execution quality remains stable.")
    p1 = public.get("p1", "Wait for clean pullback entries.")
    p2 = public.get("p2", "Respect predefined invalidation levels.")
    vol_state = public.get("vol_state", "normal")
    session_label = public.get("session_label", "Market Update")
    quarter_context = public.get("quarter_context", "near_open")
    message_tag = public.get("message_tag", "TREND_DAY_OK")
    permission_alignment = public.get("permission_alignment", "NEUTRAL")
    risk_banner = public.get("risk_banner") if isinstance(public.get("risk_banner"), dict) else {}
    weekly_range = public.get("weekly_range") if isinstance(public.get("weekly_range"), dict) else {}
    weekly_status = "Locked" if bool(weekly_range.get("range_ready")) else "Building"
    risk_multiplier = risk_banner.get("suggested_risk_multiplier")
    try:
        risk_multiplier_value = float(risk_multiplier) if risk_multiplier is not None else 1.0
    except (TypeError, ValueError):
        risk_multiplier_value = 1.0
    tier_copy_map = risk_banner.get("tier_copy") if isinstance(risk_banner.get("tier_copy"), dict) else {}
    tier_copy = str(tier_copy_map.get(tier, "Use standard risk controls."))
    risk_lines: list[str] = []
    if bool(risk_banner.get("is_blueprint_day")):
        risk_lines.append(f"- Blueprint Day: {tier_copy}")
    if bool(risk_banner.get("volume_spike")):
        ratio = risk_banner.get("volume_ratio")
        ratio_text = f" ({float(ratio):.2f}x median)" if isinstance(ratio, (int, float)) else ""
        risk_lines.append(f"- Volume Spike: {tier_copy}{ratio_text}")
    risk_lines_text = "\n".join(risk_lines)
    risk_multiplier_text = f"{risk_multiplier_value:.2f}x"
    ny_context_active = bool(public.get("ny_context_active"))
    ny_note_raw = str(public.get("ny_note") or "").strip()
    ny_line = f"NY Context: {ny_note_raw}" if ny_context_active and ny_note_raw else ""
    ny_line_block = f"{ny_line}\n" if ny_line else ""

    if tier == "basic" and manipulation_level == "high":
        r1 = manipulation_reasons[0] if manipulation_reasons else "High-volume sweep behavior detected."
        r2 = manipulation_reasons[1] if len(manipulation_reasons) > 1 else "Follow-through quality is unstable."
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} - NO TRADE (RISK)\n"
            "Manipulation Risk: HIGH\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            "Reason:\n"
            f"- {r1}\n"
            f"- {r2}\n"
            "Action: Stand aside until conditions normalize.\n"
            f"Weekly Range: {weekly_status}\n"
            f"Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"Quarter context: {quarter_context}\n"
            f"Tag: {message_tag}\n"
            f"{ny_line_block}"
            f"As of: {time_london}\n"
            "- Trading Intelligence"
        )
        return body, False

    if tier == "basic" and bias == "NO_TRADE":
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} - STAND DOWN\n"
            "Bias: NO_TRADE\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            f"Weekly Range: {weekly_status}\n"
            f"Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"Quarter context: {quarter_context}\n"
            f"Tag: {message_tag}\n"
            f"{ny_line_block}"
            "Guidance:\n"
            "Wait for directional alignment before taking new risk.\n"
            f"As of: {time_london}\n"
            "- Trading Intelligence"
        )
        return body, False

    if tier == "basic":
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} MARKET BIAS - {session_label}\n"
            f"Bias: {bias}\n"
            f"Confidence: {confidence_pct}%\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            "Context:\n"
            f"- {c1}\n"
            f"- {c2}\n"
            f"Weekly Range: {weekly_status}\n"
            f"Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"Quarter context: {quarter_context}\n"
            f"Tag: {message_tag}\n"
            f"{ny_line_block}"
            "Guidance:\n"
            "Trade only in the direction of the bias.\n"
            f"As of: {time_london}\n"
            "- Trading Intelligence"
        )
        return body, bias in {"BUY_ONLY", "SELL_ONLY"}

    if tier == "pro" and manipulation_level == "high":
        r1 = manipulation_reasons[0] if manipulation_reasons else "High-volume sweep behavior detected."
        r2 = manipulation_reasons[1] if len(manipulation_reasons) > 1 else "Follow-through quality is unstable."
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} DIRECTIONAL SETUP - PRO (RISK WARNING)\n"
            f"Bias: {bias}\n"
            f"Confidence: {confidence_pct}%\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            "Manipulation Risk: HIGH\n"
            "Reason:\n"
            f"- {r1}\n"
            f"- {r2}\n"
            f"Weekly Range: {weekly_status}\n"
            f"Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"Quarter context: {quarter_context}\n"
            f"Tag: {message_tag}\n"
            f"{ny_line_block}"
            "Guidance:\n"
            "Wait for normalization before using target details.\n"
            "H1 confirmed by M15.\n"
            f"As of: {time_london}\n"
            "- Trading Intelligence PRO"
        )
        return body, False

    if tier == "pro" and permission_alignment == "CONFLICT":
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} DIRECTIONAL SETUP - PRO (CAUTION)\n"
            f"Bias: {bias}\n"
            f"Confidence: {confidence_pct}%\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            f"Weekly Range: {weekly_status}\n"
            f"Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"Quarter context: {quarter_context}\n"
            f"Tag: {message_tag}\n"
            f"{ny_line_block}"
            "Guidance:\n"
            "Countertrend conditions detected. Reduce update frequency and avoid aggressive entries.\n"
            f"As of: {time_london}\n"
            "- Trading Intelligence PRO"
        )
        return body, False

    if tier == "pro":
        risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
        body = (
            f"{symbol} DIRECTIONAL SETUP - PRO\n"
            f"Bias: {bias}\n"
            f"Confidence: {confidence_pct}%\n"
            f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
            "Liquidity Map:\n"
            f"- {l1}\n"
            f"- {l2}\n"
            "Key Zones:\n"
            f"- Target: {target}\n"
            f"- Reaction: {reaction}\n"
            f"- Weekly Range: {weekly_status}\n"
            f"- Suggested Risk Multiplier: {risk_multiplier_text}\n"
            f"{risk_section}"
            f"- Quarter context: {quarter_context}\n"
            f"- Tag: {message_tag}\n"
            f"{ny_line_block}"
            "Guidance:\n"
            "Look for pullbacks in bias direction.\n"
            "H1 confirmed by M15.\n"
            f"As of: {time_london}\n"
            "- Trading Intelligence PRO"
        )
        return body, bias in {"BUY_ONLY", "SELL_ONLY"}

    risk_section = (f"Risk Banner:\n{risk_lines_text}\n" if risk_lines_text else "")
    body = (
        f"{symbol} EXECUTION BIAS - ELITE\n"
        f"Primary Direction: {bias}\n"
        f"Confidence: {confidence_pct}%\n"
        f"Main TF: {timeframe_main} confirm | Fast Bias ({timeframe_fast}): {fast_bias}\n"
        "Market State:\n"
        f"- {m1}\n"
        f"- {m2}\n"
        "Execution Plan:\n"
        f"- {p1}\n"
        f"- {p2}\n"
        "Risk State:\n"
        f"- Volatility: {vol_state}\n"
        f"- Manipulation: {manipulation_level}\n"
        f"- Weekly Range: {weekly_status}\n"
        f"- Suggested Risk Multiplier: {risk_multiplier_text}\n"
        f"{risk_section}"
        f"- Quarter context: {quarter_context}\n"
        f"- Tag: {message_tag}\n"
        f"{ny_line_block}"
        f"As of: {time_london}\n"
        "- Trading Intelligence ELITE"
    )
    return body, bias in {"BUY_ONLY", "SELL_ONLY"}


def _daily_bias_anchor_text(*, run: OracleRun, tier: str) -> str:
    public = run.public_json if isinstance(run.public_json, dict) else {}
    daily_permission = str(public.get("daily_permission", "")).upper()
    if daily_permission not in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"}:
        strict = str(public.get("allowed_direction_final_strict", public.get("final_allowed_basic", run.bias)))
        soft = str(public.get("allowed_direction_final_soft", public.get("final_allowed_elite", run.bias)))
        daily_permission = strict if tier == "basic" else soft
    confidence_pct = int(round(float(run.confidence) * 100))

    risk_banner = public.get("risk_banner") if isinstance(public.get("risk_banner"), dict) else {}
    weekly_range = public.get("weekly_range") if isinstance(public.get("weekly_range"), dict) else {}
    weekly_status = "Locked" if bool(weekly_range.get("range_ready")) else "Building"
    tier_copy_map = risk_banner.get("tier_copy") if isinstance(risk_banner.get("tier_copy"), dict) else {}
    tier_copy = str(tier_copy_map.get(tier, "Use standard risk controls."))

    lines = [
        f"DAILY BIAS - {run.symbol}",
        f"Daily Permission: {daily_permission}",
        f"Confidence: {confidence_pct}%",
        f"Weekly Range: {weekly_status}",
    ]
    if bool(risk_banner.get("is_blueprint_day")):
        lines.append(f"Blueprint Day: {tier_copy}")
    if bool(risk_banner.get("volume_spike")):
        ratio = risk_banner.get("volume_ratio")
        ratio_text = f" ({float(ratio):.2f}x median)" if isinstance(ratio, (int, float)) else ""
        lines.append(f"Volume Spike: {tier_copy}{ratio_text}")
    lines.append(f"As of: {format_london(datetime.now(timezone.utc))}")
    lines.append("Thread: Daily intelligence updates.")
    return "\n".join(lines)


def _select_recipients(db, *, tier_min: str = "basic"):
    min_rank = TIER_ORDER.get(tier_min, 0)
    rows = (
        db.query(User, NotificationRoute, UserSignalPref, Subscription)
        .outerjoin(NotificationRoute, NotificationRoute.user_id == User.id)
        .outerjoin(UserSignalPref, UserSignalPref.user_id == User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .filter(Subscription.status.in_(ACTIVE_SUB_STATUSES))
        .all()
    )
    recipients = []
    for user, route, pref, sub in rows:
        pref_enabled = bool(pref.telegram_enabled) if pref else False
        pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
        route_enabled = bool(route.telegram_enabled) if route else False
        route_chat = (route.telegram_chat_id or "").strip() if route else ""
        enabled = pref_enabled or route_enabled
        chat_id = pref_chat or route_chat
        if not enabled or not chat_id:
            continue

        plan = normalize_plan(sub.plan)
        if TIER_ORDER.get(plan, 0) < min_rank:
            continue
        if route is None:
            route = NotificationRoute(
                user_id=user.id,
                email_enabled=True,
                telegram_enabled=enabled,
                telegram_chat_id=chat_id,
                telegram_pin_daily_bias=True,
            )
        else:
            route.telegram_enabled = enabled
            route.telegram_chat_id = chat_id
        recipients.append((user, route, sub, plan))
    return recipients


def _select_admin_telegram_recipients(db) -> list[tuple[User, str, str]]:
    rows = (
        db.query(User, NotificationRoute, UserSignalPref, Subscription)
        .outerjoin(NotificationRoute, NotificationRoute.user_id == User.id)
        .outerjoin(UserSignalPref, UserSignalPref.user_id == User.id)
        .outerjoin(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .filter(User.role == "admin")
        .all()
    )
    recipients: list[tuple[User, str, str]] = []
    for user, route, pref, sub in rows:
        pref_enabled = bool(pref.telegram_enabled) if pref else False
        pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
        route_enabled = bool(route.telegram_enabled) if route else False
        route_chat = (route.telegram_chat_id or "").strip() if route else ""
        enabled = pref_enabled or route_enabled
        chat_id = pref_chat or route_chat
        if not enabled or not chat_id:
            continue
        plan = normalize_plan(sub.plan if sub else "elite")
        recipients.append((user, chat_id, plan))
    return recipients


def _already_sent_today(db, *, user_id, symbol: str, source: str) -> bool:
    start_utc, end_utc = _uk_day_bounds_utc(datetime.now(timezone.utc))
    return (
        db.query(DeliveryLog.id)
        .filter(DeliveryLog.user_id == user_id)
        .filter(DeliveryLog.symbol == symbol)
        .filter(DeliveryLog.source == source)
        .filter(DeliveryLog.send_status == "SENT")
        .filter(DeliveryLog.created_at >= start_utc)
        .filter(DeliveryLog.created_at <= end_utc)
        .first()
        is not None
    )


def _send_daily_permission_degraded_alerts(db, *, symbol: str, reason: str, target_utc: str | None = None) -> dict[str, int]:
    if not UK_TZ_AVAILABLE:
        reason = f"{reason} Timezone fallback is active."
    recipients = _select_admin_telegram_recipients(db)
    sent = 0
    failed = 0
    skipped = 0
    now_utc = datetime.now(timezone.utc)
    source = "daily_permission_degraded"
    alert_context = latest_oracle_alert_context(db, symbol=symbol)
    liquidity_ctx = _latest_liquidity_context(db, symbol=symbol)
    target_dt = _parse_iso_utc(target_utc)
    reason_text = reason or "08:01 daily permission degraded."
    if target_dt is not None:
        reason_text = f"{reason_text} Expected 08:01 target: {format_london(target_dt)}."
    warning_text = build_risk_stale_warning_message(
        symbol=symbol,
        detected_at=now_utc,
        permission_source=alert_context.get("permission_source") or "LONDON_0801",
        permission_stage=alert_context.get("permission_stage") or "OFFICIAL",
        daily_permission=alert_context.get("daily_permission"),
        final_allowed=alert_context.get("final_allowed"),
        h1_confirmation=alert_context.get("h1_confirmation"),
        m15_opportunity=alert_context.get("m15_opportunity"),
        confidence=alert_context.get("confidence"),
        reason=reason_text,
        magnet=_safe_float(liquidity_ctx.get("magnet_level")),
        zone_target=_safe_float(liquidity_ctx.get("zone_to_zone_target")),
        sellside=_safe_float(liquidity_ctx.get("sellside_liquidity")),
        buyside=_safe_float(liquidity_ctx.get("buyside_liquidity")),
        risk_state="DEGRADED",
        freshness=(f"STALE since {format_london(target_dt)}" if target_dt is not None else "STALE"),
    )
    for user, chat_id, plan in recipients:
        if _already_sent_today(db, user_id=user.id, symbol=symbol, source=source):
            skipped += 1
            continue
        try:
            send_result = send_telegram_message(chat_id, warning_text)
            message_id = send_result.get("message_id")
            _record_delivery_log(
                db,
                run_id=uuid.uuid4(),
                user_id=user.id,
                symbol=symbol,
                source=source,
                tier=plan,
                subscription_status="admin",
                send_status="SENT",
                detail=str(message_id),
                context_json={"reason": reason, "target_utc": target_utc},
            )
            log_audit(
                db,
                action="oracle.daily_permission.degraded_alert.sent",
                user_id=user.id,
                meta={"symbol": symbol, "reason": reason, "target_utc": target_utc},
            )
            sent += 1
        except Exception as exc:
            _record_delivery_log(
                db,
                run_id=uuid.uuid4(),
                user_id=user.id,
                symbol=symbol,
                source=source,
                tier=plan,
                subscription_status="admin",
                send_status="FAILED",
                detail=str(exc),
                context_json={"reason": reason, "target_utc": target_utc},
            )
            log_audit(
                db,
                action="oracle.daily_permission.degraded_alert.failed",
                user_id=user.id,
                meta={"symbol": symbol, "reason": reason, "error": str(exc)},
            )
            failed += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _send_thread_message_with_quota(
    db,
    *,
    user,
    route: NotificationRoute,
    sub: Subscription,
    plan: str,
    run: OracleRun | None,
    run_id: UUID,
    source: str,
    symbol: str,
    title: str,
    body: str,
    date_uk,
    anchor_text: str | None = None,
    rotate_anchor: bool = False,
    strategy_name: str = DAILY_BIAS,
    dedupe_on_run: bool = False,
    threaded: bool = True,
) -> tuple[bool, str]:
    content_hash = _message_content_hash(source=source, symbol=symbol, title=title, body=body)
    if dedupe_on_run and run is not None:
        already = (
            db.query(SignalDelivery.id)
            .filter(SignalDelivery.user_id == user.id, SignalDelivery.run_id == run.id)
            .first()
        )
        if already:
            return False, "duplicate"
    if source in NON_CRITICAL_UPDATE_SOURCES and _sent_content_hash_exists(
        db,
        user_id=user.id,
        symbol=symbol,
        source=source,
        content_hash=content_hash,
    ):
        detail = "duplicate_content_hash"
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SKIPPED",
            detail=detail,
            context_json={"title": title, "content_hash": content_hash},
        )
        db.commit()
        return False, "duplicate_content"

    strategy_value = (strategy_name or DAILY_BIAS).strip().upper()
    try:
        validate_symbol_for_strategy(symbol=symbol, strategy_name=strategy_value, tier=plan)
    except StrategyMatrixError as exc:
        detail = f"strategy_matrix_blocked:{exc.reason}"
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="skipped",
                    channel="telegram",
                    error_text=detail,
                )
            )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SKIPPED",
            detail=detail,
            context_json={
                "title": title,
                "strategy_name": strategy_value,
                "matrix_reason": exc.reason,
                "content_hash": content_hash,
            },
        )
        log_audit(
            db,
            action="signal.send.skipped_strategy_matrix",
            user_id=user.id,
            meta={
                "symbol": symbol,
                "tier": plan,
                "source": source,
                "title": title,
                "strategy_name": strategy_value,
                "matrix_reason": exc.reason,
            },
        )
        db.commit()
        return False, "strategy_blocked"

    if _non_critical_rate_limited(db, user_id=user.id, symbol=symbol, source=source):
        detail = "non_critical_rate_limited_2m"
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="skipped",
                    channel="telegram",
                    error_text=detail,
                )
            )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SKIPPED",
            detail=detail,
            context_json={"title": title, "content_hash": content_hash},
        )
        db.commit()
        return False, "rate_limited"

    usage = get_usage(db, user.id)
    limit = usage.get("limit")
    remaining = usage.get("remaining")
    if limit is not None and int(remaining or 0) <= 0:
        detail = f"usage_limit_exceeded ({usage['used']}/{usage['limit']})"
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="skipped",
                    channel="telegram",
                    error_text=detail,
                )
            )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SKIPPED",
            detail=detail,
            context_json={"title": title, "usage": usage, "content_hash": content_hash},
        )
        log_audit(
            db,
            action="signal.send.skipped_usage_limit",
            user_id=user.id,
            meta={
                "symbol": symbol,
                "tier": plan,
                "source": source,
                "title": title,
                "used": usage["used"],
                "limit": usage["limit"],
                "resets_at": usage["resets_at"],
            },
        )
        db.commit()
        return False, "quota"

    try:
        if threaded:
            update = send_thread_update(
                db,
                user_id=user.id,
                chat_id=route.telegram_chat_id,
                symbol=symbol,
                date_uk=date_uk,
                title=title,
                body=body,
                time_london=format_london(datetime.now(timezone.utc)),
                pin_bool=bool(route.telegram_pin_daily_bias),
                anchor_text=anchor_text,
                rotate_anchor=rotate_anchor,
            )
            message_id = update.get("message_id")
        else:
            plain_text = f"{title}\n{body}\nAs of: {format_london(datetime.now(timezone.utc))}"
            send_result = send_telegram_message(route.telegram_chat_id, plain_text)
            message_id = send_result.get("message_id")
            update = {"message_id": message_id, "anchor_message_id": None, "threaded": False}
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="sent",
                    channel="telegram",
                )
        )
        signal_id = None
        if message_id is not None:
            signal_id = f"thread:{run_id}:{message_id}"
        usage_after = consume_usage(
            db,
            user.id,
            n=1,
            reason=source,
            symbol=symbol,
            signal_id=signal_id,
            meta={"title": title, "run_id": str(run_id), "message_id": message_id},
        )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SENT",
            context_json={"title": title, **update, "usage": usage_after, "content_hash": content_hash},
        )
        _record_trade_audit_event(
            db,
            user_id=user.id,
            symbol=symbol,
            event_type=_event_type_for_source(source),
            tier_min=plan,
            title=title,
            message=body,
            meta_json={
                "source": source,
                "run_id": str(run_id),
                "message_id": update.get("message_id"),
                "anchor_message_id": update.get("anchor_message_id"),
                "usage": usage_after,
            },
        )
        log_audit(
            db,
            action="signal.send.sent",
            user_id=user.id,
            meta={
                "symbol": symbol,
                "tier": plan,
                "source": source,
                "title": title,
                "message_id": update.get("message_id"),
                "anchor_message_id": update.get("anchor_message_id"),
                "usage": usage_after,
            },
        )
        db.commit()
        return True, "sent"
    except UsageLimitExceeded as exc:
        db.rollback()
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="skipped",
                    channel="telegram",
                    error_text="usage_limit_exceeded_post_send",
                )
            )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="SKIPPED",
            detail="usage_limit_exceeded_post_send",
            context_json={"title": title, "usage_error": exc.payload, "content_hash": content_hash},
        )
        log_audit(
            db,
            action="signal.send.usage_limit_post_send",
            user_id=user.id,
            meta={"symbol": symbol, "tier": plan, "source": source, "title": title, "payload": exc.payload},
        )
        db.commit()
        return False, "quota"
    except IntegrityError:
        db.rollback()
        return False, "duplicate"
    except Exception as exc:
        db.rollback()
        if dedupe_on_run and run is not None:
            db.add(
                SignalDelivery(
                    user_id=user.id,
                    run_id=run.id,
                    status="failed",
                    channel="telegram",
                    error_text=str(exc),
                )
            )
        _record_delivery_log(
            db,
            run_id=run_id,
            user_id=user.id,
            symbol=symbol,
            source=source,
            tier=plan,
            subscription_status=sub.status,
            send_status="FAILED",
            detail=str(exc),
            context_json={"title": title, "content_hash": content_hash},
        )
        log_audit(
            db,
            action="signal.send.failed",
            user_id=user.id,
            meta={"symbol": symbol, "tier": plan, "source": source, "title": title, "error": str(exc)},
        )
        db.commit()
        return False, "failed"


def _maybe_create_trade_and_send_entry(db, *, user, route, sub, plan: str, run: OracleRun, actionable: bool) -> None:
    if not actionable or plan not in {"pro", "elite"}:
        return
    public = run.public_json if isinstance(run.public_json, dict) else {}
    if public.get("manipulation_level") == "high" and plan == "pro":
        return

    final_allowed = public.get("final_allowed_elite", run.bias) if plan == "elite" else public.get("final_allowed_basic", run.bias)
    if final_allowed not in {"BUY_ONLY", "SELL_ONLY"}:
        return

    if plan == "elite":
        queue_result = queue_autotrade_job_for_user(
            db,
            user_id=user.id,
            symbol=run.symbol,
            strategy_name=ZONE_TO_ZONE,
            mode="daily_bias",
        )
        if queue_result.get("ok"):
            log_audit(
                db,
                action="autotrade.job.queued_from_oracle",
                user_id=user.id,
                meta={
                    "run_id": str(run.id),
                    "symbol": run.symbol,
                    "job_id": queue_result.get("job_id"),
                },
            )
            db.commit()
            return

    candle = (run.internal_json or {}).get("candle", {})
    entry = float(candle.get("close") or 0.0)
    high = float(candle.get("high") or entry)
    low = float(candle.get("low") or entry)
    range_ = max(high - low, 0.5)
    risk = max(range_ * 0.7, 0.6)

    if final_allowed == "BUY_ONLY":
        direction = "BUY"
        sl = entry - risk
        tp1 = entry + (risk * 1.5)
        tp2 = entry + (risk * 2.5)
    else:
        direction = "SELL"
        sl = entry + risk
        tp1 = entry - (risk * 1.5)
        tp2 = entry - (risk * 2.5)

    reasons = [
        str(public.get("c1", "Directional alignment confirmed.")),
        str(public.get("c2", "Momentum passed confirmation checks.")),
    ]
    liquidity_context = _latest_liquidity_context(db, symbol=run.symbol)
    validation = validate_trade_payload(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp1,
        daily_permission=public.get("daily_permission"),
        require_h1_confirmation=True,
        h1_confirm_ok=bool(public.get("h1_confirm_ok")),
        require_liquidity_context=True,
        liquidity_context=liquidity_context,
    )
    if not validation.ok:
        logger.warning(
            "TRADE BLOCKED - %s run_id=%s user_id=%s symbol=%s phase=trade_entry",
            validation.reason,
            run.id,
            user.id,
            run.symbol,
        )
        _record_trade_audit_event(
            db,
            user_id=user.id,
            symbol=run.symbol,
            event_type="UPDATE",
            tier_min=plan,
            title="Trade Blocked",
            message=f"TRADE BLOCKED - {validation.reason}",
            meta_json={
                "phase": "trade_entry",
                "run_id": str(run.id),
                "validation_reason": validation.reason,
                "validation_details": validation.details,
            },
        )
        db.commit()
        return

    try:
        pack = create_trade_for_signal(
            db,
            user_id=user.id,
            symbol=run.symbol,
            tier=plan,
            direction=direction,
            entry=entry,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            reasons=reasons,
            opened_at_utc=datetime.now(timezone.utc),
            daily_permission=public.get("daily_permission"),
            require_h1_confirmation=True,
            h1_confirm_ok=bool(public.get("h1_confirm_ok")),
            require_liquidity_context=True,
            liquidity_context=liquidity_context,
            strategy_name=ZONE_TO_ZONE,
        )
    except ValueError as exc:
        logger.warning(
            "TRADE BLOCKED - %s run_id=%s user_id=%s symbol=%s phase=trade_entry_create",
            str(exc),
            run.id,
            user.id,
            run.symbol,
        )
        _record_trade_audit_event(
            db,
            user_id=user.id,
            symbol=run.symbol,
            event_type="UPDATE",
            tier_min=plan,
            title="Trade Blocked",
            message=f"TRADE BLOCKED - {str(exc)}",
            meta_json={"phase": "trade_entry_create", "run_id": str(run.id)},
        )
        db.commit()
        return
    _send_thread_message_with_quota(
        db,
        user=user,
        route=route,
        sub=sub,
        plan=plan,
        run=run,
        run_id=run.id,
        source="trade_entry",
        symbol=run.symbol,
        title=pack.title,
        body=pack.body,
        date_uk=pack.date_uk,
        strategy_name=ZONE_TO_ZONE,
        dedupe_on_run=False,
    )


def push_signals_for_run(run_id: UUID) -> dict[str, int]:
    with SessionLocal() as db:
        run = db.query(OracleRun).filter(OracleRun.id == run_id).first()
        if not run:
            return {"sent": 0, "failed": 0, "skipped": 0, "considered": 0}

        public = run.public_json if isinstance(run.public_json, dict) else {}
        manipulation_level = str(public.get("manipulation_level", "low"))
        manipulation_reasons = public.get("manipulation_reasons")
        if not isinstance(manipulation_reasons, list):
            manipulation_reasons = ["No abnormal manipulation signature."]
        permission_alignment = str(public.get("permission_alignment", "NEUTRAL"))
        run_strategy_name = str(public.get("strategy_name", DAILY_BIAS)).strip().upper()

        recipients = _select_recipients(db, tier_min="basic")
        sent = 0
        failed = 0
        skipped = 0

        for user, route, sub, plan in recipients:
            selected = _selected_symbols_for_user(db, user.id, plan)
            if run.symbol not in selected:
                skipped += 1
                continue
            if _should_throttle_conflict_update(
                db,
                user_id=user.id,
                symbol=run.symbol,
                plan=plan,
                permission_alignment=permission_alignment,
            ):
                _record_delivery_log(
                    db,
                    run_id=run.id,
                    user_id=user.id,
                    symbol=run.symbol,
                    source="oracle_bias",
                    tier=plan,
                    subscription_status=sub.status,
                    send_status="SKIPPED",
                    detail="conflict_throttle_daily_limit",
                    context_json={"permission_alignment": permission_alignment},
                )
                db.commit()
                skipped += 1
                continue

            body, actionable = _message_template_for_tier(
                tier=plan,
                run=run,
                time_london=format_london(datetime.now(timezone.utc)),
                manipulation_level=manipulation_level,
                manipulation_reasons=manipulation_reasons,
            )
            ok, status = _send_thread_message_with_quota(
                db,
                user=user,
                route=route,
                sub=sub,
                plan=plan,
                run=run,
                run_id=run.id,
                source="oracle_bias",
                symbol=run.symbol,
                title="Daily Bias",
                body=body,
                date_uk=to_uk_date(datetime.now(timezone.utc)),
                anchor_text=_daily_bias_anchor_text(run=run, tier=plan),
                strategy_name=run_strategy_name,
                dedupe_on_run=True,
            )
            if ok:
                sent += 1
                _maybe_create_trade_and_send_entry(db, user=user, route=route, sub=sub, plan=plan, run=run, actionable=actionable)
            elif status == "failed":
                failed += 1
            else:
                skipped += 1

        run.status = "sent" if sent > 0 else "skipped"
        db.commit()
        logger.info("Run delivery completed run_id=%s sent=%s failed=%s skipped=%s", run.id, sent, failed, skipped)
        return {"sent": sent, "failed": failed, "skipped": skipped, "considered": len(recipients)}


def _run_price_monitor_job() -> dict[str, int]:
    with SessionLocal() as db:
        updates = monitor_open_trades(db)
        if not updates:
            return {"processed_updates": 0, "sent": 0, "failed": 0, "skipped": 0}

        sent = 0
        failed = 0
        skipped = 0

        for update in updates:
            user = db.query(User).filter(User.id == update["user_id"]).first()
            sub = db.query(Subscription).filter(Subscription.user_id == update["user_id"]).first()
            route = (
                db.query(NotificationRoute)
                .filter(
                    NotificationRoute.user_id == update["user_id"],
                    NotificationRoute.telegram_enabled.is_(True),
                    NotificationRoute.telegram_chat_id.isnot(None),
                )
                .first()
            )
            if not user or not sub or not route or not user.is_active:
                skipped += 1
                continue
            plan = normalize_plan(sub.plan)
            selected = _selected_symbols_for_user(db, user.id, plan)
            if update["symbol"] not in selected:
                skipped += 1
                continue

            ok, status = _send_thread_message_with_quota(
                db,
                user=user,
                route=route,
                sub=sub,
                plan=plan,
                run=None,
                run_id=uuid.uuid4(),
                source="trade_monitor",
                symbol=update["symbol"],
                title=update["title"],
                body=update["body"],
                date_uk=update["date_uk"],
                strategy_name=ZONE_TO_ZONE,
                dedupe_on_run=False,
            )
            if ok:
                sent += 1
            elif status == "failed":
                failed += 1
            else:
                skipped += 1

        return {"processed_updates": len(updates), "sent": sent, "failed": failed, "skipped": skipped}


def _run_daily_audit_job() -> dict[str, int]:
    now_utc = datetime.now(timezone.utc)
    date_uk = to_uk_date(now_utc)
    sent = 0
    failed = 0
    skipped = 0
    considered = 0
    with SessionLocal() as db:
        recipients = _select_recipients(db, tier_min="basic")
        for user, route, sub, plan in recipients:
            selected = _selected_symbols_for_user(db, user.id, plan)
            for symbol in selected:
                considered += 1
                latest_run = (
                    db.query(OracleRun)
                    .filter(OracleRun.symbol == symbol, OracleRun.status.in_(["confirmed", "sent"]))
                    .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
                    .first()
                )
                bias = latest_run.bias if latest_run else "NO_TRADE"
                if getattr(user, "role", "user") == "admin":
                    body = build_daily_audit_message(db, user_id=user.id, symbol=symbol, date_uk=date_uk, bias=bias)
                else:
                    start_utc, end_utc = _uk_day_bounds_utc(now_utc)
                    sent_count = (
                        db.query(func.count(DeliveryLog.id))
                        .filter(DeliveryLog.user_id == user.id)
                        .filter(DeliveryLog.symbol == symbol)
                        .filter(DeliveryLog.source.in_(["oracle_bias", "admin_broadcast"]))
                        .filter(DeliveryLog.send_status == "SENT")
                        .filter(DeliveryLog.created_at >= start_utc)
                        .filter(DeliveryLog.created_at <= end_utc)
                        .scalar()
                    )
                    body = (
                        f"Daily Bias Recap - {symbol}\n"
                        f"Bias: {bias}\n"
                        f"Signals delivered: {int(sent_count or 0)}\n"
                        f"As of: {format_london(now_utc)}\n"
                        "This recap is informational only."
                    )
                ok, status = _send_thread_message_with_quota(
                    db,
                    user=user,
                    route=route,
                    sub=sub,
                    plan=plan,
                    run=None,
                    run_id=uuid.uuid4(),
                    source="daily_audit",
                    symbol=symbol,
                    title="Daily Audit",
                    body=body,
                    date_uk=date_uk,
                    strategy_name=DAILY_BIAS,
                    dedupe_on_run=False,
                )
                if ok:
                    sent += 1
                elif status == "failed":
                    failed += 1
                else:
                    skipped += 1

    return {"considered": considered, "sent": sent, "failed": failed, "skipped": skipped}


def _build_billing_renewal_text(*, plan: str, period_end_utc: datetime, now_utc: datetime) -> tuple[str, int]:
    end_local = _as_utc(period_end_utc).astimezone(UK_TZ)
    now_local = _as_utc(now_utc).astimezone(UK_TZ)
    days_left = max((end_local.date() - now_local.date()).days, 0)
    text = (
        "Subscription renewal reminder\n"
        f"Plan: {plan.upper()}\n"
        f"Renews on: {end_local.strftime('%Y-%m-%d %H:%M %Z')}\n"
        f"Days remaining: {days_left}\n\n"
        "Open dashboard -> Manage Billing"
    )
    return text, days_left


def _run_billing_renewal_reminder_job() -> None:
    now_utc = datetime.now(timezone.utc)
    window_end_utc = now_utc + timedelta(days=3)
    sent = 0
    failed = 0
    skipped = 0

    with SessionLocal() as db:
        recipients = (
            db.query(User, Subscription, NotificationRoute, UserSignalPref)
            .join(Subscription, Subscription.user_id == User.id)
            .outerjoin(NotificationRoute, NotificationRoute.user_id == User.id)
            .outerjoin(UserSignalPref, UserSignalPref.user_id == User.id)
            .filter(User.is_active.is_(True))
            .filter(Subscription.status == "active")
            .filter(Subscription.current_period_end.isnot(None))
            .all()
        )

        for user, sub, route, pref in recipients:
            pref_enabled = bool(pref.telegram_enabled) if pref else False
            pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
            route_enabled = bool(route.telegram_enabled) if route else False
            route_chat = (route.telegram_chat_id or "").strip() if route else ""
            enabled = pref_enabled or route_enabled
            chat_id = pref_chat or route_chat
            if not enabled or not chat_id:
                skipped += 1
                continue
            period_end_utc = _as_utc(sub.current_period_end)
            if period_end_utc < now_utc or period_end_utc > window_end_utc:
                skipped += 1
                continue

            window_start = period_end_utc - timedelta(days=3)
            last_sent = _as_utc(sub.last_renewal_reminder_at) if sub.last_renewal_reminder_at else None
            if last_sent and last_sent >= window_start:
                skipped += 1
                continue

            text, days_left = _build_billing_renewal_text(
                plan=normalize_plan(sub.plan),
                period_end_utc=period_end_utc,
                now_utc=now_utc,
            )
            try:
                send_telegram_message(chat_id, text)
                sub.last_renewal_reminder_at = now_utc
                sent += 1
                _record_delivery_log(
                    db,
                    run_id=uuid.uuid4(),
                    user_id=user.id,
                    symbol="BILLING",
                    source="billing_renewal_reminder",
                    tier=normalize_plan(sub.plan),
                    subscription_status=sub.status,
                    send_status="SENT",
                    context_json={
                        "current_period_end": period_end_utc.isoformat(),
                        "days_left": days_left,
                    },
                )
                log_audit(
                    db,
                    action="billing.renewal_reminder.sent",
                    user_id=user.id,
                    meta={
                        "plan": normalize_plan(sub.plan),
                        "current_period_end": period_end_utc.isoformat(),
                        "days_left": days_left,
                    },
                )
            except Exception as exc:
                failed += 1
                _record_delivery_log(
                    db,
                    run_id=uuid.uuid4(),
                    user_id=user.id,
                    symbol="BILLING",
                    source="billing_renewal_reminder",
                    tier=normalize_plan(sub.plan),
                    subscription_status=sub.status,
                    send_status="FAILED",
                    detail=str(exc),
                    context_json={"current_period_end": period_end_utc.isoformat()},
                )
                log_audit(
                    db,
                    action="billing.renewal_reminder.failed",
                    user_id=user.id,
                    meta={
                        "error": str(exc),
                        "plan": normalize_plan(sub.plan),
                        "current_period_end": period_end_utc.isoformat(),
                    },
                )
        db.commit()

    logger.info(
        "Billing renewal reminder job completed sent=%s failed=%s skipped=%s",
        sent,
        failed,
        skipped,
    )


def _latest_daily_permission_snapshot(
    db,
    *,
    symbol: str,
    date_uk=None,
    stage: str | None = None,
) -> DailyPermissionSnapshot | None:
    query = db.query(DailyPermissionSnapshot).filter(DailyPermissionSnapshot.symbol == symbol)
    if date_uk is not None:
        query = query.filter(DailyPermissionSnapshot.date_uk == date_uk)
    if stage:
        query = query.filter(DailyPermissionSnapshot.daily_permission_stage == stage)
    return query.order_by(DailyPermissionSnapshot.as_of_utc.desc(), DailyPermissionSnapshot.created_at.desc()).first()


def _active_daily_permission_snapshot(db, *, symbol: str, ref_utc: datetime | None = None) -> DailyPermissionSnapshot | None:
    now_utc = _as_utc(ref_utc or datetime.now(timezone.utc))
    local_now = now_utc.astimezone(UK_TZ)
    active_date = local_now.date()

    official = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="OFFICIAL")
    if official is not None:
        return official
    prelim = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="PRELIM")
    if prelim is not None:
        return prelim
    return None


def _daily_permission_degraded_state(
    db,
    *,
    symbol: str,
    ref_utc: datetime | None = None,
) -> tuple[bool, str | None, DailyPermissionSnapshot | None]:
    now_utc = _as_utc(ref_utc or datetime.now(timezone.utc))
    if not UK_TZ_AVAILABLE:
        return True, "Europe/London timezone unavailable (UTC fallback active).", None

    local_now = now_utc.astimezone(UK_TZ)
    active_date = local_now.date()
    row = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="OFFICIAL")

    # Grace period for the daily anchor fetch.
    if (local_now.hour, local_now.minute) < (8, 20) and local_now.date() == active_date:
        return False, None, row

    if local_now.date() == active_date and row is None:
        return True, "08:01 candle not available yet.", None

    if row is None:
        return False, None, None

    factors = row.factors_json if isinstance(row.factors_json, dict) else {}
    if bool(factors.get("missing_data")) or bool(factors.get("future_timestamp")):
        return True, row.reason or "08:01 daily permission degraded.", row
    return False, None, row


def _upsert_daily_permission_snapshot(db, *, result) -> DailyPermissionSnapshot:
    stage_value = str(result.daily_permission_stage or "OFFICIAL").upper()
    for_date_value = result.for_date or result.date_uk
    reasons_value = result.reasons if isinstance(result.reasons, list) else []
    computed_at = _as_utc(result.computed_at_utc or datetime.now(timezone.utc))
    row = (
        db.query(DailyPermissionSnapshot)
        .filter(
            DailyPermissionSnapshot.symbol == result.symbol,
            DailyPermissionSnapshot.date_uk == for_date_value,
            DailyPermissionSnapshot.daily_permission_stage == stage_value,
        )
        .first()
    )
    if row is None:
        row = DailyPermissionSnapshot(
            symbol=result.symbol,
            date_uk=for_date_value,
            for_date=for_date_value,
            daily_permission_stage=stage_value,
        )
        db.add(row)

    row.date_uk = for_date_value
    row.for_date = for_date_value
    row.timeframe = result.timeframe
    row.as_of_utc = result.as_of_utc
    row.computed_at_utc = computed_at
    row.daily_permission = result.daily_permission
    row.daily_permission_stage = stage_value
    row.permission_source = str(result.permission_source or ("ASIA" if stage_value == "PRELIM" else "LONDON_0801")).upper()
    row.official = bool(result.official if result.official is not None else stage_value == "OFFICIAL")
    row.confidence = result.confidence
    row.reasons_json = reasons_value
    row.reason = result.reason
    row.spread = result.spread
    row.volatility = result.volatility
    row.is_extreme = bool(result.is_extreme)
    row.factors_json = result.factors_json if isinstance(result.factors_json, dict) else {}
    db.flush()
    return row


def _daily_permission_anchor_text(*, symbol: str, permission: str, as_of_utc: datetime, reason: str) -> str:
    return (
        f"DAILY PERMISSION OFFICIAL (08:01 London) - {symbol}\n"
        f"Direction: {permission}\n"
        f"As of: {format_london(as_of_utc)} (UTC {_as_utc(as_of_utc).isoformat()})\n"
        f"Context: {reason}\n"
        "Thread: Daily intelligence updates."
    )


def _send_daily_permission_update(
    db,
    *,
    symbol: str,
    permission: str,
    as_of_utc: datetime,
    reason: str,
    rotate_anchor: bool,
) -> dict[str, int]:
    recipients = _select_recipients(db, tier_min="basic")
    sent = 0
    failed = 0
    skipped = 0
    anchor_text = _daily_permission_anchor_text(symbol=symbol, permission=permission, as_of_utc=as_of_utc, reason=reason)
    body = (
        f"Direction: {permission}\n"
        f"Computed from 08:01 London M1 candle.\n"
        f"Reason: {reason}\n"
        f"As of: {format_london(as_of_utc)} (UTC {_as_utc(as_of_utc).isoformat()})"
    )
    try:
        maybe_send_daily_alignment_alert(
            symbol=symbol,
            detected_at=as_of_utc,
            permission_source="LONDON_0801",
            permission_stage="OFFICIAL",
            daily_permission=permission,
            final_allowed=permission,
            h1_confirmation=None,
            m15_opportunity=None,
            confidence=None,
            reason=reason,
            magnet=None,
            zone_target=None,
            sellside=None,
            buyside=None,
            material_refresh=False,
        )
    except Exception:
        logger.exception("daily alignment telegram notify failed symbol=%s", symbol)
    for user, route, sub, plan in recipients:
        selected = _selected_symbols_for_user(db, user.id, plan)
        if symbol not in selected:
            skipped += 1
            continue
        ok, status = _send_thread_message_with_quota(
            db,
            user=user,
            route=route,
            sub=sub,
            plan=plan,
            run=None,
            run_id=uuid.uuid4(),
            source="daily_permission",
            symbol=symbol,
            title="Daily Permission (Official)",
            body=body,
            date_uk=to_uk_date(as_of_utc),
            anchor_text=anchor_text,
            rotate_anchor=rotate_anchor,
            strategy_name=DAILY_BIAS,
            dedupe_on_run=False,
        )
        if ok:
            sent += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _send_prelim_permission_update(
    db,
    *,
    symbol: str,
    permission: str,
    as_of_utc: datetime,
    reason: str,
) -> dict[str, int]:
    recipients = _select_recipients(db, tier_min="basic")
    sent = 0
    failed = 0
    skipped = 0
    body = (
        f"Stage: PRELIM (Asia)\n"
        f"Direction: {permission}\n"
        f"Reason: {reason}\n"
        f"As of: {format_london(as_of_utc)} (UTC {_as_utc(as_of_utc).isoformat()})"
    )
    for user, route, sub, plan in recipients:
        selected = _selected_symbols_for_user(db, user.id, plan)
        if symbol not in selected:
            skipped += 1
            continue
        ok, status = _send_thread_message_with_quota(
            db,
            user=user,
            route=route,
            sub=sub,
            plan=plan,
            run=None,
            run_id=uuid.uuid4(),
            source="daily_permission_prelim",
            symbol=symbol,
            title="Daily Permission Update",
            body=body,
            date_uk=to_uk_date(as_of_utc),
            strategy_name=DAILY_BIAS,
            dedupe_on_run=False,
            threaded=False,
        )
        if ok:
            sent += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _send_official_override_update(
    db,
    *,
    symbol: str,
    prelim_permission: str,
    official_permission: str,
    as_of_utc: datetime,
    rotate_anchor: bool = False,
) -> dict[str, int]:
    recipients = _select_recipients(db, tier_min="basic")
    sent = 0
    failed = 0
    skipped = 0
    body = (
        f"London override applied for {symbol}\n"
        f"PRELIM (Asia): {prelim_permission}\n"
        f"OFFICIAL (08:01 London): {official_permission}\n"
        f"As of: {format_london(as_of_utc)} (UTC {_as_utc(as_of_utc).isoformat()})"
    )
    for user, route, sub, plan in recipients:
        selected = _selected_symbols_for_user(db, user.id, plan)
        if symbol not in selected:
            skipped += 1
            continue
        ok, status = _send_thread_message_with_quota(
            db,
            user=user,
            route=route,
            sub=sub,
            plan=plan,
            run=None,
            run_id=uuid.uuid4(),
            source="daily_permission_override",
            symbol=symbol,
            title="London Override",
            body=body,
            date_uk=to_uk_date(as_of_utc),
            anchor_text=None,
            rotate_anchor=rotate_anchor,
            strategy_name=DAILY_BIAS,
            dedupe_on_run=False,
            threaded=True,
        )
        if ok:
            sent += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def _opportunity_to_oracle_run(db, *, opp: OpportunityResult) -> OracleRun:
    final_allowed = opp.final_allowed if opp.final_allowed in {"BUY_ONLY", "SELL_ONLY"} else "NO_TRADE"
    compute_time_utc = datetime.now(timezone.utc)
    fast_bias_m1 = str(opp.public_json.get("fast_bias_m1") or opp.daily_permission)
    ny_note = str(opp.public_json.get("ny_note") or "").strip()
    c2 = "Opportunity layer: M15 setup with H1 confirmation gate."
    if ny_note:
        c2 = f"{c2} {ny_note}"
    public = {
        "session_label": "M15 Opportunity",
        "signal_timeframe": opp.timeframe_signal,
        "confirm_timeframe": opp.timeframe_confirm,
        "fast_bias": fast_bias_m1,
        "fast_bias_m1": fast_bias_m1,
        "fast_bias_m1_time_utc": opp.public_json.get("fast_bias_m1_time_utc"),
        "ny_context_active": bool(opp.public_json.get("ny_context_active")),
        "ny_confidence_delta": opp.public_json.get("ny_confidence_delta"),
        "ny_note": ny_note,
        "micro_confidence_delta": opp.public_json.get("micro_confidence_delta"),
        "daily_permission": opp.daily_permission,
        "daily_permission_as_of_utc": opp.public_json.get("daily_permission_as_of_utc"),
        "permission_stage": opp.public_json.get("permission_stage", "OFFICIAL"),
        "permission_source": opp.public_json.get("permission_source", "LONDON_0801"),
        "permission_lock_time_london": opp.public_json.get("permission_lock_time_london"),
        "permission_for_date_uk": opp.public_json.get("permission_for_date_uk"),
        "conflict_with_prelim": bool(opp.public_json.get("conflict_with_prelim", False)),
        "conflict_note": opp.public_json.get("conflict_note"),
        "opportunity_direction": opp.opportunity_direction,
        "confirm_ok": bool(opp.h1_confirm_ok),
        "confirm_tf": opp.timeframe_confirm,
        "final_allowed_basic": final_allowed,
        "final_allowed_elite": final_allowed,
        "allowed_direction_final_strict": final_allowed,
        "allowed_direction_final_soft": final_allowed,
        "daily_bias_raw": opp.daily_permission,
        "daily_bias": _regime_from_direction(opp.daily_permission),
        "permission_alignment": "ALIGNED" if opp.aligned else "CONFLICT",
        "message_tag": "TREND_DAY_OK" if final_allowed != "NO_TRADE" else "NO_TRADE_FILTER",
        "c1": opp.reason,
        "c2": c2,
        "l1": f"M15 close: {opp.public_json.get('m15_close')}",
        "l2": f"H1 close: {opp.public_json.get('h1_close')}",
        "target": "-",
        "reaction": "-",
        "m1": "Opportunity direction evaluated on M15.",
        "m2": "Final decision must align with daily permission.",
        "p1": "Use only aligned opportunities.",
        "p2": "Reject if H1 confirmation fails.",
        "reason_basic": opp.reason,
        "atr_h1": opp.public_json.get("atr_h1"),
        "adr_d1": opp.public_json.get("adr_d1"),
        "risk_gate_pass": bool(opp.public_json.get("risk_gate_pass", True)),
        "news_gate_pass": bool(opp.public_json.get("news_gate_pass", True)),
        "weekly_range": opp.public_json.get("weekly_range") if isinstance(opp.public_json.get("weekly_range"), dict) else {},
        "risk_banner": opp.public_json.get("risk_banner") if isinstance(opp.public_json.get("risk_banner"), dict) else {},
        "last_compute_at_utc": compute_time_utc.isoformat(),
    }
    run = OracleRun(
        symbol=opp.symbol,
        timeframe=opp.timeframe_signal,
        as_of_utc=opp.as_of_utc,
        bias=opp.opportunity_direction if opp.opportunity_direction in {"BUY_ONLY", "SELL_ONLY"} else "NO_TRADE",
        confidence=opp.confidence,
        manipulation_score=0,
        manipulation_level="low",
        internal_json=opp.internal_json,
        public_json=public,
        status="confirmed" if final_allowed in {"BUY_ONLY", "SELL_ONLY"} else "skipped",
    )
    db.add(run)
    db.flush()
    _upsert_gold_regime_snapshot(db, run)
    return run


def _upsert_mt5_candle_row(
    db,
    *,
    symbol: str,
    timeframe: str,
    time_utc: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None,
) -> bool:
    row = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == timeframe,
            MT5Candle.time_utc == time_utc,
        )
        .first()
    )
    created = row is None
    if row is None:
        row = MT5Candle(symbol=symbol, timeframe=timeframe, time_utc=time_utc)
        db.add(row)

    row.open = float(open_)
    row.high = float(high)
    row.low = float(low)
    row.close = float(close)
    row.volume = float(volume) if volume is not None else None
    return created


def _ingest_oracle_source_candles(db, *, symbol: str, timeframes: tuple[str, ...] = ("M1", "M15", "H1")) -> list[dict]:
    provider = get_data_provider()
    symbol_value = symbol.strip().upper()
    results: list[dict] = []
    latest_ingested_at: datetime | None = None

    for timeframe in timeframes:
        tf = timeframe.strip().upper()
        try:
            candle = provider.get_latest_closed_candle(symbol=symbol_value, timeframe=tf)
            candle_time = _as_utc(candle.time_utc)
            created = _upsert_mt5_candle_row(
                db,
                symbol=symbol_value,
                timeframe=tf,
                time_utc=candle_time,
                open_=float(candle.open),
                high=float(candle.high),
                low=float(candle.low),
                close=float(candle.close),
                volume=float(candle.volume) if candle.volume is not None else None,
            )
            latest_ingested_at = candle_time if latest_ingested_at is None else max(latest_ingested_at, candle_time)
            results.append(
                {
                    "ok": True,
                    "symbol": symbol_value,
                    "timeframe": tf,
                    "time_open_utc": candle_time.isoformat(),
                    "created": created,
                }
            )
        except Exception as exc:
            logger.exception("oracle source ingest failed symbol=%s timeframe=%s", symbol_value, tf)
            results.append({"ok": False, "symbol": symbol_value, "timeframe": tf, "error": str(exc)})

    if latest_ingested_at is not None:
        now_value = now_utc()
        status_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol_value).first()
        existing_offset = None
        if status_row is not None and status_row.broker_offset_seconds is not None:
            existing_offset = int(status_row.broker_offset_seconds)
        if status_row is None:
            status_row = MT5IngestStatus(
                symbol=symbol_value,
                last_ingested_at=latest_ingested_at,
                broker_offset_seconds=existing_offset if existing_offset is not None else 0,
                broker_offset_detected_at=now_value if existing_offset is not None else None,
            )
            db.add(status_row)
        else:
            if _as_utc(status_row.last_ingested_at) < latest_ingested_at:
                status_row.last_ingested_at = latest_ingested_at
            db.add(status_row)

    return results


def _create_candidate_run(db, symbol: str) -> OracleRun:
    candidate = compute_hourly_candidate(db, symbol=symbol)
    weekly_snapshot = compute_weekly_range_snapshot(db, symbol=symbol, as_of_utc=candidate.as_of_utc)
    quarter_snapshot, permission = _compute_and_store_permission_state(
        db,
        symbol=symbol,
        daily_bias_raw=candidate.bias,
        daily_confidence=candidate.confidence,
        as_of_utc=candidate.as_of_utc,
    )
    _upsert_weekly_range_snapshot(db, snapshot=weekly_snapshot)
    public = dict(candidate.public_json)
    public.update(
        {
            "daily_bias_raw": candidate.bias,
            "quarterly_bias": quarter_snapshot.quarterly_bias,
            "quarter_key": quarter_snapshot.quarter_key,
            "quarter_context": quarter_snapshot.premium_discount,
            "quarterly_confidence": quarter_snapshot.confidence,
            "permission_alignment": permission.alignment,
            "message_tag": permission.message_tag,
            "allowed_direction_final_strict": permission.allowed_direction_final_strict,
            "allowed_direction_final_soft": permission.allowed_direction_final_soft,
            "final_allowed_basic": permission.allowed_direction_final_strict,
            "final_allowed_elite": permission.allowed_direction_final_soft,
            "permission_details": permission.details,
            "weekly_range": {
                "symbol": weekly_snapshot.symbol,
                "week_key": weekly_snapshot.week_key,
                "week_start_uk": weekly_snapshot.week_start_uk.isoformat(),
                "high": weekly_snapshot.high,
                "low": weekly_snapshot.low,
                "mid": weekly_snapshot.mid,
                "range_ready": weekly_snapshot.range_ready,
                "status": "Locked" if weekly_snapshot.range_ready else "Building",
                "as_of_utc": weekly_snapshot.as_of_utc.isoformat(),
                "meta_json": weekly_snapshot.meta_json,
            },
        }
    )
    run = OracleRun(
        symbol=candidate.symbol,
        timeframe=candidate.timeframe,
        as_of_utc=candidate.as_of_utc,
        bias=candidate.bias,
        confidence=candidate.confidence,
        manipulation_score=0,
        manipulation_level="low",
        internal_json=candidate.internal_json,
        public_json=public,
        status="candidate",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    logger.info("Created oracle run id=%s symbol=%s bias=%s", run.id, run.symbol, run.bias)
    return run


def run_oracle_hourly_job(symbol: str | None = None, *, dispatch_signals: bool = False) -> dict:
    symbol_value = (symbol or settings.ORACLE_SYMBOL or "XAUUSD").upper()
    with SessionLocal() as db:
        ingest = _ingest_oracle_source_candles(db, symbol=symbol_value, timeframes=("M1", "M15", "H1"))
        permission_row, backfill_result = _ensure_daily_permission_snapshot(db, symbol=symbol_value)
        opp = compute_opportunity_with_h1_confirmation(
            db,
            symbol=symbol_value,
            daily_permission=permission_row.daily_permission,
        )
        opp.public_json["daily_permission_as_of_utc"] = _as_utc(permission_row.as_of_utc).isoformat()
        opp.public_json["permission_stage"] = str(permission_row.daily_permission_stage or "OFFICIAL").upper()
        opp.public_json["permission_source"] = str(permission_row.permission_source or "LONDON_0801").upper()
        opp.public_json["permission_lock_time_london"] = _permission_lock_time_london(permission_row.for_date or permission_row.date_uk)
        opp.public_json["permission_for_date_uk"] = (permission_row.for_date or permission_row.date_uk).isoformat()
        prelim_row = _latest_daily_permission_snapshot(
            db,
            symbol=symbol_value,
            date_uk=(permission_row.for_date or permission_row.date_uk),
            stage="PRELIM",
        )
        prelim_permission = prelim_row.daily_permission if prelim_row else None
        conflict_with_prelim = bool(
            str(permission_row.daily_permission_stage or "").upper() == "OFFICIAL"
            and prelim_permission in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"}
            and prelim_permission != permission_row.daily_permission
        )
        opp.public_json["conflict_with_prelim"] = conflict_with_prelim
        if conflict_with_prelim:
            opp.public_json["conflict_note"] = (
                f"London override: PRELIM {prelim_permission} -> OFFICIAL {permission_row.daily_permission}"
            )
        try:
            weekly_snapshot = compute_weekly_range_snapshot(db, symbol=symbol_value, as_of_utc=opp.as_of_utc)
            _upsert_weekly_range_snapshot(db, snapshot=weekly_snapshot)
            opp.public_json["weekly_range"] = {
                "symbol": weekly_snapshot.symbol,
                "week_key": weekly_snapshot.week_key,
                "week_start_uk": weekly_snapshot.week_start_uk.isoformat(),
                "high": weekly_snapshot.high,
                "low": weekly_snapshot.low,
                "mid": weekly_snapshot.mid,
                "range_ready": weekly_snapshot.range_ready,
                "status": "Locked" if weekly_snapshot.range_ready else "Building",
                "as_of_utc": weekly_snapshot.as_of_utc.isoformat(),
                "meta_json": weekly_snapshot.meta_json,
            }
        except Exception:
            logger.exception("weekly range compute failed symbol=%s as_of=%s", symbol_value, _as_utc(opp.as_of_utc).isoformat())

        if opp.public_json.get("atr_h1") is None or opp.public_json.get("adr_d1") is None:
            logger.warning(
                "risk stats missing symbol=%s atr_h1=%s adr_d1=%s",
                symbol_value,
                opp.public_json.get("atr_h1"),
                opp.public_json.get("adr_d1"),
            )
        run = _opportunity_to_oracle_run(db, opp=opp)
        refresh_targets_for_all_symbols(db, symbols=[symbol_value], reason="manual_run", tiers=["pro", "elite"])
        db.commit()
        delivery = {"sent": 0, "failed": 0, "skipped": 0, "considered": 0}
        if dispatch_signals and opp.final_allowed in {"BUY_ONLY", "SELL_ONLY"}:
            delivery = push_signals_for_run(run.id)
        return {
            "run_id": str(run.id),
            "symbol": run.symbol,
            "daily_permission": permission_row.daily_permission,
            "daily_permission_as_of_utc": _as_utc(permission_row.as_of_utc).isoformat(),
            "opportunity_direction": opp.opportunity_direction,
            "final_allowed": opp.final_allowed,
            "h1_confirm_ok": opp.h1_confirm_ok,
            "confidence": opp.confidence,
            "status": run.status,
            "dispatch_signals": dispatch_signals,
            "ingest": ingest,
            "daily_permission_backfill": backfill_result,
            "delivery": delivery,
        }


def run_oracle_for_symbols(symbols: list[str], *, dispatch_signals: bool = False) -> dict:
    normalized: list[str] = []
    for value in symbols:
        symbol = value.strip().upper()
        if symbol and symbol not in normalized:
            normalized.append(symbol)

    results: list[dict] = []
    for symbol in normalized:
        try:
            results.append(run_oracle_hourly_job(symbol=symbol, dispatch_signals=dispatch_signals))
        except Exception as exc:
            logger.exception("Hourly oracle failed for symbol=%s", symbol)
            results.append({"symbol": symbol, "ok": False, "error": str(exc)})
    overall_ok = all("error" not in row for row in results)
    return {"ok": overall_ok, "runs": results}


def run_oracle_all_symbols_job() -> dict:
    return run_oracle_for_symbols(enabled_symbols_from_settings(), dispatch_signals=False)


def _send_magnet_update(db, *, symbol: str, reason: str) -> dict[str, int]:
    magnet = latest_magnet_state(db, symbol=symbol)
    if magnet is None:
        return {"sent": 0, "failed": 0, "skipped": 0}

    state = magnet.state_json if isinstance(magnet.state_json, dict) else {}
    current = state.get("current") if isinstance(state.get("current"), dict) else {}
    hit = state.get("hit") if isinstance(state.get("hit"), dict) else {}
    side = str(current.get("side") or magnet.magnet_side or "").upper()
    price = current.get("price", magnet.magnet_price)
    hit_note = ""
    if hit:
        hit_note = f"\nHit: {hit.get('hit_side')} @ {hit.get('hit_price')} ({hit.get('reason')})"

    body = (
        f"Liquidity Magnet Update ({symbol})\n"
        f"Current magnet: {side} {price}\n"
        f"Next zone target: {magnet.zone_to_zone_target}\n"
        f"Sellside: {magnet.sellside_liquidity} | Buyside: {magnet.buyside_liquidity}\n"
        f"Reason: {reason}{hit_note}\n"
        f"As of: {format_london(magnet.as_of_utc)}"
    )
    try:
        alert_context = latest_oracle_alert_context(db, symbol=symbol)
        alert_result = maybe_send_liquidity_target_alert(
            symbol=symbol,
            as_of_utc=magnet.as_of_utc,
            reason=reason,
            magnet=_safe_float(price),
            zone_target=_safe_float(magnet.zone_to_zone_target),
            sellside=_safe_float(magnet.sellside_liquidity),
            buyside=_safe_float(magnet.buyside_liquidity),
            daily_permission=alert_context.get("daily_permission"),
            permission_source=alert_context.get("permission_source"),
            permission_stage=alert_context.get("permission_stage"),
            final_allowed=alert_context.get("final_allowed"),
            h1_confirmation=alert_context.get("h1_confirmation"),
            m15_opportunity=alert_context.get("m15_opportunity"),
            confidence=_safe_float(alert_context.get("confidence")),
            risk_state=alert_context.get("risk_state"),
        )
        if alert_result.get("status") == "alert_sent":
            _mark_runner_telegram_sent(db, sent_at=_as_utc(magnet.as_of_utc))
    except Exception:
        logger.exception("liquidity target telegram notify failed symbol=%s", symbol)
    recipients = _select_recipients(db, tier_min="pro")
    sent = 0
    failed = 0
    skipped = 0
    for user, route, sub, plan in recipients:
        selected = _selected_symbols_for_user(db, user.id, plan)
        if symbol not in selected:
            skipped += 1
            continue
        ok, status = _send_thread_message_with_quota(
            db,
            user=user,
            route=route,
            sub=sub,
            plan=plan,
            run=None,
            run_id=uuid.uuid4(),
            source="magnet_update",
            symbol=symbol,
            title="Magnet Update",
            body=body,
            date_uk=to_uk_date(datetime.now(timezone.utc)),
            strategy_name=ZONE_TO_ZONE,
            dedupe_on_run=False,
        )
        if ok:
            sent += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return {"sent": sent, "failed": failed, "skipped": skipped}


def run_daily_permission_all_symbols_job() -> dict:
    if not UK_TZ_AVAILABLE:
        logger.warning("08:01 daily permission job skipped: Europe/London timezone unavailable.")
        return {"ok": False, "reason": "timezone_unavailable"}

    local_now = london_now()
    if (local_now.hour, local_now.minute) < (8, 2):
        return {"ok": True, "skipped": True, "reason": "before_official_lock"}

    symbols = enabled_symbols_from_settings()
    rows: list[dict] = []
    with SessionLocal() as db:
        for symbol in symbols:
            try:
                ref_now = _as_utc(now_utc())
                active_date = ref_now.astimezone(UK_TZ).date()
                prev = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="OFFICIAL")
                prev_permission = prev.daily_permission if prev else None
                prelim = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="PRELIM")
                prelim_permission = prelim.daily_permission if prelim else None
                row, backfill_result = _ensure_daily_permission_snapshot(db, symbol=symbol, ref_utc=ref_now)
                if str(row.daily_permission_stage).upper() != "OFFICIAL":
                    row = _upsert_daily_permission_snapshot(
                        db,
                        result=compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref_now),
                    )
                db.commit()
                notify = True
                bias_changed = prev is None or prev_permission != row.daily_permission
                counts = {"sent": 0, "failed": 0, "skipped": 0}
                if notify:
                    counts = _send_daily_permission_update(
                        db,
                        symbol=symbol,
                        permission=row.daily_permission,
                        as_of_utc=row.as_of_utc,
                        reason=row.reason or "08:01 permission computed.",
                        rotate_anchor=bias_changed,
                    )
                    db.commit()
                override_counts = {"sent": 0, "failed": 0, "skipped": 0}
                conflict_with_prelim = bool(
                    prelim_permission
                    and prelim_permission in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"}
                    and prelim_permission != row.daily_permission
                )
                if conflict_with_prelim:
                    override_counts = _send_official_override_update(
                        db,
                        symbol=symbol,
                        prelim_permission=prelim_permission or "NO_TRADE",
                        official_permission=row.daily_permission,
                        as_of_utc=row.as_of_utc,
                    )
                    db.commit()
                degraded, degraded_reason, _row = _daily_permission_degraded_state(db, symbol=symbol)
                degraded_alerts = {"sent": 0, "failed": 0, "skipped": 0}
                if degraded:
                    target_utc = None
                    factors = row.factors_json if isinstance(row.factors_json, dict) else {}
                    if isinstance(factors.get("target_utc"), str):
                        target_utc = factors.get("target_utc")
                    degraded_alerts = _send_daily_permission_degraded_alerts(
                        db,
                        symbol=symbol,
                        reason=degraded_reason or "08:01 daily permission degraded.",
                        target_utc=target_utc,
                    )
                    db.commit()
                rows.append(
                    {
                        "symbol": symbol,
                        "daily_permission": row.daily_permission,
                        "as_of_utc": _as_utc(row.as_of_utc).isoformat(),
                        "bias_changed": bias_changed,
                        "notified": notify,
                        "delivery": counts,
                        "degraded": degraded,
                        "degraded_reason": degraded_reason,
                        "degraded_alerts": degraded_alerts,
                        "backfill": backfill_result,
                        "conflict_with_prelim": conflict_with_prelim,
                        "override_delivery": override_counts,
                    }
                )
            except Exception as exc:
                db.rollback()
                logger.exception("daily permission job failed symbol=%s", symbol)
                rows.append({"symbol": symbol, "ok": False, "error": str(exc)})
    return {"ok": True, "items": rows}


def run_prelim_permission_all_symbols_job() -> dict:
    if not UK_TZ_AVAILABLE:
        logger.warning("PRELIM daily permission job skipped: Europe/London timezone unavailable.")
        return {"ok": False, "reason": "timezone_unavailable"}

    local_now = london_now()
    if (local_now.hour, local_now.minute) >= (8, 2):
        return {"ok": True, "skipped": True, "reason": "official_window_started"}

    symbols = enabled_symbols_from_settings()
    rows: list[dict] = []
    with SessionLocal() as db:
        for symbol in symbols:
            try:
                ref_now = _as_utc(now_utc())
                active_date = ref_now.astimezone(UK_TZ).date()
                prev = _latest_daily_permission_snapshot(db, symbol=symbol, date_uk=active_date, stage="PRELIM")
                prev_permission = prev.daily_permission if prev else None
                row, _ = _ensure_daily_permission_snapshot(db, symbol=symbol, ref_utc=ref_now)
                if str(row.daily_permission_stage).upper() != "PRELIM":
                    row = _upsert_daily_permission_snapshot(
                        db,
                        result=compute_prelim_permission_from_asia(db, symbol=symbol, ref_utc=ref_now),
                    )
                db.commit()
                changed = prev is None or prev_permission != row.daily_permission
                delivery = {"sent": 0, "failed": 0, "skipped": 0}
                if changed:
                    delivery = _send_prelim_permission_update(
                        db,
                        symbol=symbol,
                        permission=row.daily_permission,
                        as_of_utc=row.as_of_utc,
                        reason=row.reason or "Asia prelim permission updated.",
                    )
                    db.commit()
                rows.append(
                    {
                        "symbol": symbol,
                        "stage": row.daily_permission_stage,
                        "daily_permission": row.daily_permission,
                        "as_of_utc": _as_utc(row.as_of_utc).isoformat(),
                        "changed": changed,
                        "delivery": delivery,
                    }
                )
            except Exception as exc:
                db.rollback()
                logger.exception("prelim permission job failed symbol=%s", symbol)
                rows.append({"symbol": symbol, "ok": False, "error": str(exc)})
    return {"ok": True, "items": rows}


def run_daily_permission_degraded_check_job() -> dict:
    symbols = enabled_symbols_from_settings()
    items: list[dict] = []
    with SessionLocal() as db:
        for symbol in symbols:
            try:
                ref_now = _as_utc(now_utc())
                _row, backfill_result = _ensure_daily_permission_snapshot(db, symbol=symbol, ref_utc=ref_now)
                degraded, reason, row = _daily_permission_degraded_state(db, symbol=symbol, ref_utc=ref_now)
                alerts = {"sent": 0, "failed": 0, "skipped": 0}
                if degraded:
                    factors = row.factors_json if (row and isinstance(row.factors_json, dict)) else {}
                    target_utc = factors.get("target_utc") if isinstance(factors.get("target_utc"), str) else None
                    alerts = _send_daily_permission_degraded_alerts(
                        db,
                        symbol=symbol,
                        reason=reason or "08:01 daily permission degraded.",
                        target_utc=target_utc,
                    )
                    db.commit()
                items.append(
                    {
                        "symbol": symbol,
                        "degraded": degraded,
                        "reason": reason,
                        "backfill": backfill_result,
                        "alerts": alerts,
                    }
                )
            except Exception as exc:
                db.rollback()
                logger.exception("daily permission degraded check failed symbol=%s", symbol)
                items.append({"symbol": symbol, "ok": False, "error": str(exc)})
    return {"ok": True, "items": items}


def run_m15_opportunity_all_symbols_job() -> dict:
    symbols = enabled_symbols_from_settings()
    rows: list[dict] = []
    with SessionLocal() as db:
        for symbol in symbols:
            try:
                permission_row = _active_daily_permission_snapshot(db, symbol=symbol)
                backfill_result = None
                if permission_row is None:
                    permission_row, backfill_result = _ensure_daily_permission_snapshot(db, symbol=symbol)
                permission = permission_row.daily_permission if permission_row else "NO_TRADE"
                previous_run = (
                    db.query(OracleRun)
                    .filter(OracleRun.symbol == symbol, OracleRun.timeframe == "M15")
                    .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
                    .first()
                )
                opp = compute_opportunity_with_h1_confirmation(db, symbol=symbol, daily_permission=permission)
                opp.public_json["daily_permission_as_of_utc"] = (
                    _as_utc(permission_row.as_of_utc).isoformat() if permission_row else None
                )
                permission_stage = None
                permission_source = None
                if permission_row is not None:
                    permission_stage = str(permission_row.daily_permission_stage or "OFFICIAL").upper()
                    permission_source = str(permission_row.permission_source or "LONDON_0801").upper()
                    opp.public_json["permission_stage"] = permission_stage
                    opp.public_json["permission_source"] = permission_source
                    opp.public_json["permission_lock_time_london"] = _permission_lock_time_london(
                        permission_row.for_date or permission_row.date_uk
                    )
                    opp.public_json["permission_for_date_uk"] = (
                        permission_row.for_date or permission_row.date_uk
                    ).isoformat()
                    prelim_row = _latest_daily_permission_snapshot(
                        db,
                        symbol=symbol,
                        date_uk=(permission_row.for_date or permission_row.date_uk),
                        stage="PRELIM",
                    )
                    prelim_permission = prelim_row.daily_permission if prelim_row else None
                    conflict_with_prelim = bool(
                        str(permission_row.daily_permission_stage or "").upper() == "OFFICIAL"
                        and prelim_permission in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"}
                        and prelim_permission != permission_row.daily_permission
                    )
                    opp.public_json["conflict_with_prelim"] = conflict_with_prelim
                    if conflict_with_prelim:
                        opp.public_json["conflict_note"] = (
                            f"London override: PRELIM {prelim_permission} -> OFFICIAL {permission_row.daily_permission}"
                        )
                new_signature = _normalized_opportunity_signature_from_opp(
                    permission=permission,
                    permission_stage=permission_stage,
                    permission_source=permission_source,
                    opp=opp,
                )
                old_signature = _normalized_opportunity_signature_from_run(previous_run)
                snapshot_changed = old_signature != new_signature
                run = _opportunity_to_oracle_run(db, opp=opp)
                db.commit()
                sent_counts = {"sent": 0, "failed": 0, "skipped": 0, "considered": 0}
                ingest_publish = _publish_opportunity_signal_to_ingest(
                    db=db,
                    symbol=symbol,
                    permission=permission,
                    permission_stage=permission_stage,
                    permission_source=permission_source,
                    opp=opp,
                )
                alert_delivery = {
                    "status": "not_applicable",
                    "reason": "signal_not_refreshed",
                }
                if bool(ingest_publish.get("ok")) and bool(ingest_publish.get("refresh_needed")):
                    alert_delivery = _maybe_send_aligned_signal_alert(
                        db,
                        signal_payload=ingest_publish.get("payload"),
                        material_refresh=True,
                    )
                if snapshot_changed and run.public_json.get("final_allowed_basic") in {"BUY_ONLY", "SELL_ONLY"}:
                    sent_counts = push_signals_for_run(run.id)
                rows.append(
                    {
                        "symbol": symbol,
                        "opportunity_direction": opp.opportunity_direction,
                        "daily_permission": permission,
                        "final_allowed": opp.final_allowed,
                        "h1_confirm_ok": opp.h1_confirm_ok,
                        "as_of_utc": opp.as_of_utc.isoformat(),
                        "run_id": str(run.id),
                        "daily_permission_backfill": backfill_result,
                        "snapshot_changed": snapshot_changed,
                        "ingest_publish": ingest_publish,
                        "telegram_alert": alert_delivery,
                        "delivery": sent_counts,
                    }
                )
            except Exception as exc:
                db.rollback()
                logger.exception("m15 opportunity job failed symbol=%s", symbol)
                rows.append({"symbol": symbol, "ok": False, "error": str(exc)})
    return {"ok": True, "items": rows}


def run_market_ingest_job(*, timeframes: list[str] | tuple[str, ...] | None = None) -> dict:
    normalized_timeframes = [str(tf).strip().upper() for tf in (timeframes or ["M1"]) if str(tf).strip()]
    if not normalized_timeframes:
        normalized_timeframes = ["M1"]
    run_magnet_checks = "M1" in normalized_timeframes
    newest_ingested: dict[tuple[str, str], datetime] = {}
    symbols_for_compute: dict[str, dict[str, datetime]] = {}
    compute_runs: list[dict] = []
    with SessionLocal() as db:
        try:
            results = ingest_latest_candles(db, timeframes=normalized_timeframes)
            for item in results:
                if not item.get("ok"):
                    continue
                symbol_value = str(item.get("symbol") or "").strip().upper()
                timeframe = str(item.get("timeframe") or "").strip().upper()
                candle_time = _parse_iso_utc(item.get("time_open_utc"))
                if not symbol_value or not timeframe or candle_time is None:
                    continue
                key = (symbol_value, timeframe)
                previous = newest_ingested.get(key)
                if previous is None or candle_time > previous:
                    newest_ingested[key] = candle_time
            magnet_updates: list[dict] = []
            if run_magnet_checks:
                for symbol in enabled_symbols_from_settings():
                    latest_m1 = (
                        db.query(MT5Candle)
                        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == "M1")
                        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
                        .first()
                    )
                    if not latest_m1:
                        continue
                    before_state = latest_magnet_state(db, symbol=symbol)
                    price = float(latest_m1.close)
                    hit_pro = maybe_refresh_targets_on_magnet_hit(
                        db,
                        symbol=symbol,
                        bid=price,
                        ask=price,
                        m1_close=price,
                        tier="pro",
                    )
                    hit_elite = maybe_refresh_targets_on_magnet_hit(
                        db,
                        symbol=symbol,
                        bid=price,
                        ask=price,
                        m1_close=price,
                        tier="elite",
                    )
                    if bool(hit_pro.get("hit")) or bool(hit_elite.get("hit")):
                        after_state = latest_magnet_state(db, symbol=symbol)
                        changed = _magnet_row_changed(symbol, before=before_state, after=after_state)
                        if changed:
                            updates = _send_magnet_update(
                                db,
                                symbol=symbol,
                                reason="magnet taken; next magnet recomputed",
                            )
                            magnet_updates.append(
                                {
                                    "symbol": symbol,
                                    "pro": hit_pro,
                                    "elite": hit_elite,
                                    "changed": True,
                                    "delivery": updates,
                                }
                            )
                        else:
                            magnet_updates.append(
                                {
                                    "symbol": symbol,
                                    "pro": hit_pro,
                                    "elite": hit_elite,
                                    "changed": False,
                                    "delivery": {"sent": 0, "failed": 0, "skipped": 0},
                                }
                            )

            for (symbol_value, timeframe), candle_time in newest_ingested.items():
                state = (
                    db.query(OracleProcessingState)
                    .filter(
                        OracleProcessingState.symbol == symbol_value,
                        OracleProcessingState.timeframe == timeframe,
                    )
                    .first()
                )
                last_processed = _as_utc(state.last_processed_candle_utc) if state and state.last_processed_candle_utc else None
                if last_processed is None or candle_time > last_processed:
                    symbol_entry = symbols_for_compute.setdefault(symbol_value, {})
                    symbol_entry[timeframe] = candle_time

            db.commit()
            ok_count = sum(1 for item in results if item.get("ok"))
            fail_count = len(results) - ok_count

            for symbol_value, timeframe_map in symbols_for_compute.items():
                try:
                    compute_result = run_oracle_hourly_job(symbol=symbol_value, dispatch_signals=False)
                    compute_runs.append({"symbol": symbol_value, "ok": True, **compute_result})
                    for timeframe, candle_time in timeframe_map.items():
                        _upsert_oracle_processing_state(
                            db,
                            symbol=symbol_value,
                            timeframe=timeframe,
                            candle_time_utc=candle_time,
                        )
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    logger.exception("oracle compute after ingest failed symbol=%s", symbol_value)
                    compute_runs.append({"symbol": symbol_value, "ok": False, "error": str(exc)})

            logger.info(
                "market ingest scheduler run timeframes=%s ok=%s failed=%s compute_runs=%s",
                ",".join(normalized_timeframes),
                ok_count,
                fail_count,
                len(compute_runs),
            )
            return {
                "ok": fail_count == 0,
                "timeframes": normalized_timeframes,
                "ingested": ok_count,
                "failed": fail_count,
                "items": results,
                "magnet_updates": magnet_updates,
                "compute_runs": compute_runs,
                "compute_candidates": sorted(symbols_for_compute.keys()),
            }
        except Exception as exc:
            db.rollback()
            logger.exception("market ingest scheduler failed")
            return {"ok": False, "error": str(exc)}


def run_london_open_backfill_job() -> dict:
    if not UK_TZ_AVAILABLE:
        logger.warning("08:01 backfill job skipped: Europe/London timezone unavailable.")
        return {"ok": False, "reason": "timezone_unavailable"}

    local_now = london_now()
    in_capture_window = ((local_now.hour == 7 and local_now.minute >= 58) or (local_now.hour == 8 and local_now.minute <= 20))
    if not in_capture_window:
        return {
            "ok": True,
            "skipped": True,
            "reason": "outside_capture_window",
            "now_london": local_now.isoformat(),
        }

    date_uk = london_now().date()
    rows: list[dict] = []
    with SessionLocal() as db:
        runner_row = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()
        if runner_row is not None and not bool(runner_row.mt5_connected):
            reason = (runner_row.last_error or "Runner MT5 disconnected.").strip()
            logger.warning("08:01 backfill skipped: runner mt5 disconnected error=%s", reason)
            return {
                "ok": False,
                "skipped": True,
                "reason": "runner_mt5_disconnected",
                "runner_last_error": reason,
                "date_uk": date_uk.isoformat(),
            }
        for symbol in enabled_symbols_from_settings():
            try:
                result = backfill_london_open_m1_window(db, symbol=symbol, date_uk=date_uk)
                if bool(result.get("ok")):
                    permission_row, _ = _ensure_daily_permission_snapshot(db, symbol=symbol, ref_utc=now_utc())
                    factors = permission_row.factors_json if isinstance(permission_row.factors_json, dict) else {}
                    result["daily_permission"] = permission_row.daily_permission
                    result["daily_permission_as_of_utc"] = _as_utc(permission_row.as_of_utc).isoformat()
                    result["daily_permission_stage"] = str(permission_row.daily_permission_stage or "OFFICIAL").upper()
                    result["permission_source"] = str(permission_row.permission_source or "LONDON_0801").upper()
                    result["stale_reasons"] = factors.get("stale_reasons", [])
                rows.append({"symbol": symbol, **result})
            except Exception as exc:
                logger.exception("08:01 backfill scheduler failed symbol=%s", symbol)
                rows.append({"symbol": symbol, "ok": False, "error": str(exc)})
        db.commit()
    ok_count = sum(1 for row in rows if row.get("ok"))
    logger.info("08:01 backfill scheduler run date_uk=%s ok=%s total=%s", date_uk.isoformat(), ok_count, len(rows))
    return {"ok": ok_count == len(rows), "date_uk": date_uk.isoformat(), "items": rows}


def run_targets_h1_refresh_job() -> dict:
    with SessionLocal() as db:
        try:
            before: dict[str, dict] = {}
            for symbol in enabled_symbols_from_settings():
                row = latest_magnet_state(db, symbol=symbol)
                if row:
                    before[symbol] = {
                        "magnet_price": row.magnet_price,
                        "magnet_side": row.magnet_side,
                    }
            rows = refresh_targets_for_all_symbols(db, reason="h1_close", tiers=["pro", "elite"])
            notifications: list[dict] = []
            for symbol in enabled_symbols_from_settings():
                after = latest_magnet_state(db, symbol=symbol)
                if not after:
                    continue
                prior = before.get(symbol)
                changed = (
                    prior is None
                    or _magnet_changed_significantly(symbol, prior.get("magnet_price"), after.magnet_price)
                    or str(prior.get("magnet_side")) != str(after.magnet_side)
                )
                if changed:
                    notifications.append({"symbol": symbol, **_send_magnet_update(db, symbol=symbol, reason="H1 close refresh")})
            db.commit()
            ok_count = sum(1 for row in rows if row.get("ok"))
            logger.info("targets H1 refresh run total=%s ok=%s", len(rows), ok_count)
            return {"ok": True, "rows": rows, "notifications": notifications}
        except Exception as exc:
            db.rollback()
            logger.exception("targets H1 refresh failed")
            return {"ok": False, "error": str(exc)}


def run_targets_safety_refresh_job() -> dict:
    with SessionLocal() as db:
        try:
            rows = refresh_targets_for_all_symbols(db, reason="safety_5m", tiers=["pro", "elite"])
            db.commit()
            ok_count = sum(1 for row in rows if row.get("ok"))
            logger.info("targets safety refresh run total=%s ok=%s", len(rows), ok_count)
            return {"ok": True, "rows": rows}
        except Exception as exc:
            db.rollback()
            logger.exception("targets safety refresh failed")
            return {"ok": False, "error": str(exc)}


def run_oracle_london_open_job() -> dict:
    results: list[dict] = []
    for symbol in enabled_symbols_from_settings():
        try:
            results.append(run_oracle_hourly_job(symbol=symbol, dispatch_signals=False))
        except Exception as exc:
            logger.exception("London open oracle failed for symbol=%s", symbol)
            results.append({"symbol": symbol, "ok": False, "error": str(exc)})
    return {"ok": True, "runs": results}


def run_oracle_confirm_job(run_id: str) -> dict:
    run_uuid = UUID(run_id)
    with SessionLocal() as db:
        run = db.query(OracleRun).filter(OracleRun.id == run_uuid).first()
        if not run:
            logger.warning("Confirmation skipped run_id=%s reason=not_found", run_id)
            return {"ok": False, "reason": "run_not_found"}
        if run.status not in {"candidate", "confirmed"}:
            return {"ok": False, "reason": f"already_{run.status}"}

        confirm = confirm_with_m15(
            db,
            symbol=run.symbol,
            candidate_bias=run.bias,
            candidate_as_of_utc=run.as_of_utc,
        )
        db.add(
            OracleConfirmation(
                run_id=run.id,
                as_of_utc=confirm.as_of_utc,
                confirm_ok=confirm.confirm_ok,
                confirm_reason_json=confirm.reason_json,
            )
        )

        quarter_snapshot, permission = _compute_and_store_permission_state(
            db,
            symbol=run.symbol,
            daily_bias_raw=run.bias,
            daily_confidence=run.confidence,
            as_of_utc=confirm.as_of_utc,
        )
        weekly_snapshot = compute_weekly_range_snapshot(db, symbol=run.symbol, as_of_utc=confirm.as_of_utc)
        _upsert_weekly_range_snapshot(db, snapshot=weekly_snapshot)

        if confirm.confirm_ok:
            final_basic = permission.allowed_direction_final_strict
            final_elite = permission.allowed_direction_final_soft
        else:
            final_basic = "NO_TRADE"
            final_elite = "NO_TRADE"

        if confirm.manipulation_level == "high":
            final_basic = "NO_TRADE"

        public = run.public_json if isinstance(run.public_json, dict) else {}
        public.update(
            {
                "confirm_ok": confirm.confirm_ok,
                "confirm_tf": "M15",
                "confirm_as_of_utc": confirm.as_of_utc.isoformat(),
                "manipulation_score": confirm.manipulation_score,
                "manipulation_level": confirm.manipulation_level,
                "manipulation_reasons": confirm.manipulation_reasons,
                "volume_state": confirm.m15_volume_state,
                "final_allowed_basic": final_basic,
                "final_allowed_elite": final_elite,
                "allowed_direction_final_strict": permission.allowed_direction_final_strict,
                "allowed_direction_final_soft": permission.allowed_direction_final_soft,
                "quarterly_bias": quarter_snapshot.quarterly_bias,
                "quarter_key": quarter_snapshot.quarter_key,
                "quarter_context": quarter_snapshot.premium_discount,
                "quarterly_confidence": quarter_snapshot.confidence,
                "permission_alignment": permission.alignment,
                "message_tag": permission.message_tag,
                "permission_details": permission.details,
                "daily_bias": _regime_from_direction(run.bias),
                "daily_bias_raw": run.bias,
                "weekly_range": {
                    "symbol": weekly_snapshot.symbol,
                    "week_key": weekly_snapshot.week_key,
                    "week_start_uk": weekly_snapshot.week_start_uk.isoformat(),
                    "high": weekly_snapshot.high,
                    "low": weekly_snapshot.low,
                    "mid": weekly_snapshot.mid,
                    "range_ready": weekly_snapshot.range_ready,
                    "status": "Locked" if weekly_snapshot.range_ready else "Building",
                    "as_of_utc": weekly_snapshot.as_of_utc.isoformat(),
                    "meta_json": weekly_snapshot.meta_json,
                },
            }
        )
        run.public_json = public
        run.manipulation_score = confirm.manipulation_score
        run.manipulation_level = confirm.manipulation_level

        if not confirm.confirm_ok:
            run.status = "skipped"
            _upsert_gold_regime_snapshot(db, run)
            db.commit()
            logger.info("Run skipped after confirmation run_id=%s", run_id)
            return {"ok": True, "confirmed": False, "run_id": run_id}

        run.status = "confirmed"
        _upsert_gold_regime_snapshot(db, run)
        db.commit()
        logger.info("Run confirmed run_id=%s manipulation=%s", run_id, confirm.manipulation_level)

    counts = push_signals_for_run(run_uuid)
    return {"ok": True, "confirmed": True, "run_id": run_id, **counts}


def run_oracle_now(symbol: str | None = None) -> dict:
    return run_oracle_hourly_job(symbol=symbol)


def run_confirm_now(run_id: str) -> dict:
    return run_oracle_confirm_job(run_id=run_id)


def run_price_monitor_now() -> dict:
    return _run_price_monitor_job()


def run_daily_audit_now() -> dict:
    return _run_daily_audit_job()


def recompute_quarterly_snapshots(symbol: str | None = None) -> dict:
    targets = [symbol.strip().upper()] if symbol else enabled_symbols_from_settings()
    rows: list[dict] = []
    with SessionLocal() as db:
        for sym in targets:
            try:
                snapshot = compute_quarterly_snapshot(db, symbol=sym)
                _upsert_quarterly_snapshot(db, snapshot=snapshot)
                db.commit()
                rows.append(
                    {
                        "symbol": sym,
                        "quarter_key": snapshot.quarter_key,
                        "quarterly_bias": snapshot.quarterly_bias,
                        "premium_discount": snapshot.premium_discount,
                        "confidence": snapshot.confidence,
                        "as_of_utc": snapshot.as_of_utc.isoformat(),
                    }
                )
            except Exception as exc:
                db.rollback()
                rows.append({"symbol": sym, "ok": False, "error": str(exc)})
    return {"ok": True, "snapshots": rows}


def recompute_permission_today(symbol: str | None = None) -> dict:
    targets = [symbol.strip().upper()] if symbol else enabled_symbols_from_settings()
    rows: list[dict] = []
    with SessionLocal() as db:
        for sym in targets:
            try:
                latest_run = (
                    db.query(OracleRun)
                    .filter(OracleRun.symbol == sym)
                    .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
                    .first()
                )
                if latest_run:
                    daily_bias_raw = latest_run.bias
                    daily_conf = latest_run.confidence
                    as_of_utc = latest_run.as_of_utc
                else:
                    candidate = compute_hourly_candidate(db, symbol=sym)
                    daily_bias_raw = candidate.bias
                    daily_conf = candidate.confidence
                    as_of_utc = candidate.as_of_utc

                quarter_snapshot, decision = _compute_and_store_permission_state(
                    db,
                    symbol=sym,
                    daily_bias_raw=daily_bias_raw,
                    daily_confidence=daily_conf,
                    as_of_utc=as_of_utc,
                )
                db.commit()
                rows.append(
                    {
                        "symbol": sym,
                        "date_uk": decision.date_uk.isoformat(),
                        "daily_bias_raw": decision.daily_bias_raw,
                        "quarterly_bias": decision.quarterly_bias,
                        "allowed_direction_final": decision.allowed_direction_final,
                        "allowed_direction_final_strict": decision.allowed_direction_final_strict,
                        "allowed_direction_final_soft": decision.allowed_direction_final_soft,
                        "alignment": decision.alignment,
                        "confidence_final": decision.confidence_final,
                        "message_tag": decision.message_tag,
                        "quarter_key": quarter_snapshot.quarter_key,
                        "as_of_utc": decision.as_of_utc.isoformat(),
                    }
                )
            except Exception as exc:
                db.rollback()
                rows.append({"symbol": sym, "ok": False, "error": str(exc)})
    return {"ok": True, "permissions": rows}


def broadcast_admin_message(
    symbol: str,
    tier_min: str,
    title: str,
    message: str,
    strategy_name: str = DAILY_BIAS,
) -> dict:
    symbol_value = symbol.strip().upper()
    strategy_value = (strategy_name or DAILY_BIAS).strip().upper()
    with SessionLocal() as db:
        run = OracleRun(
            symbol=symbol_value,
            timeframe="ADMIN",
            as_of_utc=datetime.now(timezone.utc),
            bias="NO_TRADE",
            confidence=1.0,
            manipulation_score=0,
            manipulation_level="low",
            internal_json={"title": title, "message": message, "strategy_name": strategy_value},
            public_json={
                "title": title,
                "message": message,
                "strategy_name": strategy_value,
                "final_allowed_basic": "NO_TRADE",
                "final_allowed_elite": "NO_TRADE",
            },
            status="confirmed",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        recipients = _select_recipients(db, tier_min=tier_min)
        sent = 0
        failed = 0
        skipped = 0
        for user, route, sub, plan in recipients:
            selected = _selected_symbols_for_user(db, user.id, plan)
            if symbol_value not in selected:
                skipped += 1
                continue
            ok, status = _send_thread_message_with_quota(
                db,
                user=user,
                route=route,
                sub=sub,
                plan=plan,
                run=run,
                run_id=run.id,
                source="admin_broadcast",
                symbol=symbol_value,
                title=title,
                body=message,
                date_uk=to_uk_date(datetime.now(timezone.utc)),
                strategy_name=strategy_value,
                dedupe_on_run=True,
            )
            if ok:
                sent += 1
            elif status == "failed":
                failed += 1
            else:
                skipped += 1

        run.status = "sent" if sent > 0 else "skipped"
        db.commit()
        return {
            "ok": True,
            "run_id": str(run.id),
            "tier_min": tier_min,
            "strategy_name": strategy_value,
            "sent": sent,
            "failed": failed,
            "skipped": skipped,
            "considered": len(recipients),
        }


def _recover_pending_confirmations() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        pending = db.query(OracleRun).filter(OracleRun.status == "candidate").all()
        for run in pending:
            base = _as_utc(run.created_at or run.as_of_utc)
            run_at = base + timedelta(minutes=settings.ORACLE_CONFIRM_DELAY_MINUTES)
            if run_at < now:
                run_at = now + timedelta(seconds=5)
            try:
                _schedule_confirm_job(run.id, run_at, replace_existing=False)
            except Exception:
                logger.exception("Failed to recover pending confirmation run_id=%s", run.id)


def start_oracle_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler and _scheduler.running:
            return
        scheduler = BackgroundScheduler(timezone=UK_TZ)
        if settings.MARKET_INGEST_HEARTBEAT_ENABLED:
            scheduler.add_job(
                run_market_ingest_job,
                trigger="interval",
                seconds=60,
                kwargs={"timeframes": ["M1"]},
                id="market_ingest_m1_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(
                run_market_ingest_job,
                trigger="interval",
                minutes=5,
                kwargs={"timeframes": ["M5"]},
                id="market_ingest_m5_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(
                run_market_ingest_job,
                trigger=CronTrigger(minute="0,15,30,45", timezone=UK_TZ),
                kwargs={"timeframes": ["M15"]},
                id="market_ingest_m15_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(
                run_market_ingest_job,
                trigger=CronTrigger(minute=0, timezone=UK_TZ),
                kwargs={"timeframes": ["H1"]},
                id="market_ingest_h1_job",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        scheduler.add_job(
            run_oracle_all_symbols_job,
            trigger=CronTrigger(minute=2, timezone=UK_TZ),
            id="oracle_hourly_snapshot_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_daily_permission_all_symbols_job,
            trigger=CronTrigger(hour=8, minute=2, timezone=UK_TZ),
            id="daily_permission_0801_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_prelim_permission_all_symbols_job,
            trigger=CronTrigger(minute="*/15", timezone=UK_TZ),
            id="daily_permission_prelim_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_london_open_backfill_job,
            trigger=CronTrigger(hour=7, minute="58,59", timezone=UK_TZ),
            id="daily_permission_backfill_pre_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_london_open_backfill_job,
            trigger=CronTrigger(hour=8, minute="0-20", timezone=UK_TZ),
            id="daily_permission_backfill_capture_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_daily_permission_degraded_check_job,
            trigger=CronTrigger(hour=8, minute=20, timezone=UK_TZ),
            id="daily_permission_degraded_check_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_m15_opportunity_all_symbols_job,
            trigger=CronTrigger(minute="*/15", timezone=UK_TZ),
            id="opportunity_m15_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_targets_h1_refresh_job,
            trigger=CronTrigger(minute=2, timezone=UK_TZ),
            id="targets_h1_refresh_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            run_targets_safety_refresh_job,
            trigger="interval",
            minutes=5,
            id="targets_safety_refresh_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _run_price_monitor_job,
            trigger="interval",
            seconds=max(int(settings.ORACLE_PRICE_MONITOR_INTERVAL_SECONDS or 60), 30),
            id="oracle_price_monitor_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _run_daily_audit_job,
            trigger=CronTrigger(
                hour=int(settings.ORACLE_DAILY_AUDIT_HOUR),
                minute=int(settings.ORACLE_DAILY_AUDIT_MINUTE),
                timezone=UK_TZ,
            ),
            id="oracle_daily_audit_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.add_job(
            _run_billing_renewal_reminder_job,
            trigger=CronTrigger(
                hour=int(settings.BILLING_RENEWAL_REMINDER_HOUR),
                minute=int(settings.BILLING_RENEWAL_REMINDER_MINUTE),
                timezone=UK_TZ,
            ),
            id="billing_renewal_reminder_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info("Oracle scheduler started timezone=%s", UK_TZ)
        _recover_pending_confirmations()


def stop_oracle_scheduler() -> None:
    global _scheduler
    with _lock:
        if not _scheduler:
            return
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("Oracle scheduler stopped.")
        _scheduler = None

if __name__ == "__main__":
    import time
    logging.basicConfig(level=logging.INFO)
    start_oracle_scheduler()
    print("oracle scheduler started")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        stop_oracle_scheduler()
        print("oracle scheduler stopped")
