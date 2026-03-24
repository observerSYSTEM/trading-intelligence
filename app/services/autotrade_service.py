from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.symbols import allowed_symbols_for_tier, normalize_plan
from app.db.models import (
    AuditEvent,
    AutoTradeGlobalControl,
    AutoTradeSymbolControl,
    NotificationRoute,
    OracleRun,
    PositionState,
    Subscription,
    Trade,
    TradeEvent,
    TradeExec,
    TradeJob,
    UserRiskSetting,
    User,
    UserSignalPref,
    UserSymbolPreference,
)
from app.services.audit import log_audit
from app.services.oracle_exec import build_execution_instruction
from app.services.strategy_matrix import DAILY_BIAS, StrategyMatrixError, validate_symbol_for_strategy
from app.services.symbol_preferences import get_user_enabled_symbols
from app.services.telegram_service import send_thread_update
from app.services.trade_validation import validate_trade_payload
from app.services.trade_tracker import create_trade_for_signal, format_london, to_uk_date

ACTIVE_SUB_STATUSES = {"active", "trialing"}
ACTIONABLE_DIRECTIONS = {"BUY_ONLY": "BUY", "SELL_ONLY": "SELL"}
JOB_DISPATCHABLE_STATUSES = {"queued"}
STRATEGIES_REQUIRING_H1_CONFIRMATION = {DAILY_BIAS, "ZONE_TO_ZONE"}
STRATEGIES_REQUIRING_LIQUIDITY_CONTEXT = {"ZONE_TO_ZONE", "LIQ_SWEEP", "VOL_MANIP"}

logger = logging.getLogger(__name__)


def _uk_tz():
    try:
        return ZoneInfo("Europe/London")
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401

            return ZoneInfo("Europe/London")
        except Exception:
            return timezone.utc


UK_TZ = _uk_tz()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(dt)


def _uk_day_bounds(now_utc: datetime) -> tuple[datetime, datetime]:
    now_local = _as_utc(now_utc).astimezone(UK_TZ)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _record_audit_event(
    db: Session,
    *,
    user_id,
    symbol: str | None,
    action: str,
    allowed: bool,
    reason_json: dict | None = None,
) -> None:
    db.add(
        AuditEvent(
            user_id=user_id,
            symbol=(symbol or None),
            action=action[:128],
            allowed=bool(allowed),
            reason_json=reason_json or {},
        )
    )
    db.flush()


def _blocked(
    db: Session,
    *,
    user_id,
    symbol: str,
    reason: str,
    plan: str,
    run_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload = {"reason": reason, "plan": plan}
    if run_id:
        payload["run_id"] = run_id
    if extra:
        payload.update(extra)
    _record_audit_event(
        db,
        user_id=user_id,
        symbol=symbol,
        action="autotrade.queue_precheck",
        allowed=False,
        reason_json=payload,
    )
    return {"ok": False, **payload}


def _get_or_create_user_risk_settings(db: Session, *, user_id) -> UserRiskSetting:
    row = db.query(UserRiskSetting).filter(UserRiskSetting.user_id == user_id).first()
    if row:
        return row
    row = UserRiskSetting(
        user_id=user_id,
        risk_mode="fixed",
        risk_value=float(settings.AUTOTRADE_DEFAULT_VOLUME),
        max_trades_day=int(settings.AUTOTRADE_MAX_TRADES_PER_DAY),
        max_daily_loss=3.0,
        max_open_trades=int(settings.AUTOTRADE_MAX_OPEN_TRADES_PER_SYMBOL),
        max_lot=float(settings.AUTOTRADE_MAX_VOLUME),
        allowed_symbols_json=[],
        avoid_mondays=False,
        block_on_volume_spike=False,
        news_filter_enabled=bool(settings.AUTOTRADE_NEWS_BLOCK_ENABLED),
        news_block_minutes=int(settings.ORACLE_NEWS_BLOCK_MINUTES),
    )
    db.add(row)
    db.flush()
    return row


def _get_global_control(db: Session) -> AutoTradeGlobalControl:
    row = db.query(AutoTradeGlobalControl).filter(AutoTradeGlobalControl.id == 1).first()
    if row:
        return row
    row = AutoTradeGlobalControl(id=1, autotrade_enabled=False)
    db.add(row)
    db.flush()
    return row


def _is_symbol_globally_enabled(db: Session, *, symbol: str) -> bool:
    row = db.query(AutoTradeSymbolControl).filter(AutoTradeSymbolControl.symbol == symbol).first()
    if not row:
        return True
    return bool(row.autotrade_enabled)


def is_autotrade_enabled_for_user_symbol(
    db: Session,
    *,
    user_id,
    symbol: str,
) -> tuple[bool, str, str]:
    symbol_value = symbol.strip().upper()
    if not bool(settings.AUTOTRADE_ENABLED):
        return False, "autotrade_disabled", "basic"

    global_control = _get_global_control(db)
    if not bool(global_control.autotrade_enabled):
        return False, "global_kill_switch", "basic"

    if not _is_symbol_globally_enabled(db, symbol=symbol_value):
        return False, "symbol_global_kill_switch", "basic"

    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if not sub or (sub.status or "").lower() not in ACTIVE_SUB_STATUSES:
        return False, "subscription_inactive", "basic"

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not bool(user.is_active):
        return False, "user_inactive", "basic"
    if getattr(user, "role", "user") != "admin":
        return False, "autotrade_admin_only", "basic"
    admin_email = (settings.AUTOTRADE_ADMIN_EMAIL or "").strip().lower()
    if admin_email and str(user.email or "").strip().lower() != admin_email:
        return False, "autotrade_admin_email_mismatch", "basic"

    plan = normalize_plan(sub.plan)
    if plan != "elite":
        return False, "tier_not_allowed", plan

    if symbol_value not in allowed_symbols_for_tier(plan):
        return False, "symbol_not_allowed_for_tier", plan

    if not bool(sub.autotrade_enabled):
        return False, "user_kill_switch", plan

    selected_symbols = get_user_enabled_symbols(db, user_id, plan)
    if symbol_value not in selected_symbols:
        return False, "symbol_not_enabled", plan

    pref = (
        db.query(UserSymbolPreference)
        .filter(UserSymbolPreference.user_id == user_id, UserSymbolPreference.symbol == symbol_value)
        .first()
    )
    if pref and not bool(pref.autotrade_enabled):
        return False, "symbol_kill_switch", plan

    return True, "ok", plan


def set_global_autotrade_enabled(db: Session, *, enabled: bool) -> dict:
    row = _get_global_control(db)
    row.autotrade_enabled = bool(enabled)
    db.add(row)
    db.flush()
    return {"autotrade_enabled": bool(row.autotrade_enabled)}


def set_global_symbol_autotrade_enabled(db: Session, *, symbol: str, enabled: bool) -> dict:
    symbol_value = symbol.strip().upper()
    row = db.query(AutoTradeSymbolControl).filter(AutoTradeSymbolControl.symbol == symbol_value).first()
    if not row:
        row = AutoTradeSymbolControl(symbol=symbol_value, autotrade_enabled=bool(enabled))
        db.add(row)
    else:
        row.autotrade_enabled = bool(enabled)
        db.add(row)
    db.flush()
    return {"symbol": symbol_value, "autotrade_enabled": bool(row.autotrade_enabled)}


def set_user_autotrade_enabled(db: Session, *, user_id, enabled: bool) -> dict:
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if not sub:
        sub = Subscription(user_id=user_id, plan="basic", status="inactive", usage_count=0)
        db.add(sub)
        db.flush()
    sub.autotrade_enabled = bool(enabled)
    db.add(sub)
    db.flush()
    return {"user_id": str(user_id), "autotrade_enabled": bool(sub.autotrade_enabled)}


def set_symbol_autotrade_enabled(db: Session, *, user_id, symbol: str, enabled: bool) -> dict:
    symbol_value = symbol.strip().upper()
    pref = (
        db.query(UserSymbolPreference)
        .filter(UserSymbolPreference.user_id == user_id, UserSymbolPreference.symbol == symbol_value)
        .first()
    )
    if not pref:
        pref = UserSymbolPreference(
            user_id=user_id,
            symbol=symbol_value,
            enabled=True,
            autotrade_enabled=bool(enabled),
        )
        db.add(pref)
    else:
        pref.autotrade_enabled = bool(enabled)
        db.add(pref)
    db.flush()
    return {
        "user_id": str(user_id),
        "symbol": symbol_value,
        "autotrade_enabled": bool(pref.autotrade_enabled),
    }


def _latest_run_for_symbol(db: Session, symbol: str) -> OracleRun | None:
    return (
        db.query(OracleRun)
        .filter(OracleRun.symbol == symbol)
        .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
        .first()
    )


def _extract_public(run: OracleRun) -> dict:
    if isinstance(run.public_json, dict):
        return run.public_json
    return {}


def _risk_gate_result(run: OracleRun, *, risk_settings: UserRiskSetting) -> tuple[list[str], float, dict]:
    public = _extract_public(run)
    reasons: list[str] = []
    risk_banner = public.get("risk_banner") if isinstance(public.get("risk_banner"), dict) else {}
    news_window = public.get("news_blocked_window") if isinstance(public.get("news_blocked_window"), dict) else {}
    blueprint_day = bool(risk_banner.get("is_blueprint_day"))
    volume_spike = bool(risk_banner.get("volume_spike"))
    suggested_multiplier = _safe_float(risk_banner.get("suggested_risk_multiplier"))
    if suggested_multiplier is None or suggested_multiplier <= 0:
        suggested_multiplier = 1.0
    if blueprint_day:
        suggested_multiplier = min(suggested_multiplier, 0.5)
    if volume_spike:
        suggested_multiplier = min(suggested_multiplier, 0.25)

    atr_h1 = _safe_float(public.get("atr_h1"))
    if atr_h1 is not None and (atr_h1 < float(settings.ORACLE_ATR_H1_MIN) or atr_h1 > float(settings.ORACLE_ATR_H1_MAX)):
        reasons.append("atr_out_of_bounds")

    adr_d1 = _safe_float(public.get("adr_d1"))
    if adr_d1 is not None and (adr_d1 < float(settings.ORACLE_ADR_D1_MIN) or adr_d1 > float(settings.ORACLE_ADR_D1_MAX)):
        reasons.append("adr_out_of_bounds")

    news_gate_pass = bool(public.get("news_gate_pass", True))
    if bool(risk_settings.news_filter_enabled):
        if not news_gate_pass:
            reasons.append("news_gate_blocked")
        elif news_window:
            window_minutes = _safe_float(news_window.get("minutes_to_event"))
            news_block_minutes = float(risk_settings.news_block_minutes or settings.ORACLE_NEWS_BLOCK_MINUTES)
            if window_minutes is not None and abs(window_minutes) <= max(news_block_minutes, 0):
                reasons.append("news_window_blocked")

    if bool(risk_settings.avoid_mondays) and blueprint_day:
        reasons.append("blueprint_day_blocked")

    if bool(risk_settings.block_on_volume_spike) and volume_spike:
        reasons.append("volume_spike_blocked")

    if bool(settings.AUTOTRADE_BLOCK_HIGH_RISK) and str(public.get("manipulation_level", "")).lower() == "high":
        reasons.append("manipulation_high")

    context = {
        "blueprint_day": blueprint_day,
        "volume_spike": volume_spike,
        "news_gate_pass": news_gate_pass,
        "news_block_minutes": int(risk_settings.news_block_minutes or settings.ORACLE_NEWS_BLOCK_MINUTES),
        "news_blocked_window": news_window,
    }
    return reasons, float(suggested_multiplier), context


def _trades_today(db: Session, *, user_id) -> int:
    now_utc = datetime.now(timezone.utc)
    start_utc, end_utc = _uk_day_bounds(now_utc)
    value = (
        db.query(func.count(TradeJob.id))
        .filter(TradeJob.user_id == user_id)
        .filter(TradeJob.created_at >= start_utc, TradeJob.created_at < end_utc)
        .filter(TradeJob.status.in_(["queued", "dispatched", "filled"]))
        .scalar()
    )
    return int(value or 0)


def _daily_loss_today(db: Session, *, user_id) -> float:
    now_utc = datetime.now(timezone.utc)
    start_utc, end_utc = _uk_day_bounds(now_utc)
    rows = (
        db.query(Trade.rr_realized)
        .filter(Trade.user_id == user_id)
        .filter(Trade.closed_at.isnot(None))
        .filter(Trade.closed_at >= start_utc, Trade.closed_at < end_utc)
        .all()
    )
    loss = 0.0
    for row in rows:
        rr = _safe_float(row[0] if isinstance(row, tuple) else row)
        if rr is not None and rr < 0:
            loss += abs(rr)
    return round(loss, 4)


def _open_positions_total(db: Session, *, user_id) -> int:
    value = db.query(func.count(PositionState.id)).filter(PositionState.user_id == user_id).scalar()
    return int(value or 0)


def _open_positions_for_symbol(db: Session, *, user_id, symbol: str) -> int:
    value = (
        db.query(func.count(PositionState.id))
        .filter(PositionState.user_id == user_id, PositionState.symbol == symbol)
        .scalar()
    )
    return int(value or 0)


def _build_job_payload(job: TradeJob) -> dict:
    reason_json = job.reason_json if isinstance(job.reason_json, dict) else {}
    instruction = reason_json.get("instruction") if isinstance(reason_json.get("instruction"), dict) else {}
    return {
        "id": str(job.id),
        "user_id": str(job.user_id),
        "symbol": job.symbol,
        "side": job.side,
        "volume": float(job.volume),
        "entry_type": job.entry_type,
        "entry_price": job.entry_price,
        "sl": job.sl,
        "tp": job.tp,
        "status": job.status,
        "created_at": _as_utc(job.created_at).isoformat() if job.created_at else None,
        "expires_at": _as_utc(job.expires_at).isoformat() if job.expires_at else None,
        "instruction": instruction,
        "reason_json": reason_json,
    }


def _send_trade_update(
    db: Session,
    *,
    user_id,
    symbol: str,
    title: str,
    body: str,
    anchor_text: str | None = None,
) -> None:
    pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user_id).first()
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user_id).first()
    pref_enabled = bool(pref.telegram_enabled) if pref else False
    pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
    route_enabled = bool(route.telegram_enabled) if route else False
    route_chat = (route.telegram_chat_id or "").strip() if route else ""
    enabled = pref_enabled or route_enabled
    chat_id = pref_chat or route_chat
    pin_daily_bias = bool(route.telegram_pin_daily_bias) if route else True
    if not enabled or not chat_id:
        return

    now_utc = datetime.now(timezone.utc)
    send_thread_update(
        db,
        user_id=user_id,
        chat_id=chat_id,
        symbol=symbol,
        date_uk=to_uk_date(now_utc),
        title=title,
        body=body,
        time_london=format_london(now_utc),
        pin_bool=pin_daily_bias,
        anchor_text=anchor_text,
    )


def _final_direction_for_elite(run: OracleRun) -> str:
    public = _extract_public(run)
    return str(public.get("allowed_direction_final_soft", public.get("final_allowed_elite", run.bias))).upper()


def _confirm_tf(run: OracleRun) -> str:
    public = _extract_public(run)
    return str(public.get("confirm_tf", "")).upper()


def _strategy_requires_h1_confirmation(strategy_name: str | None) -> bool:
    strategy_value = str(strategy_name or DAILY_BIAS).strip().upper()
    return strategy_value in STRATEGIES_REQUIRING_H1_CONFIRMATION


def _strategy_requires_liquidity_context(strategy_name: str | None) -> bool:
    strategy_value = str(strategy_name or DAILY_BIAS).strip().upper()
    return strategy_value in STRATEGIES_REQUIRING_LIQUIDITY_CONTEXT


def _build_liquidity_context(
    *,
    public: dict | None,
    instruction: dict | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    public_data = public if isinstance(public, dict) else {}
    out["magnet_level"] = _safe_float(
        public_data.get("liquidity_target")
        or public_data.get("magnet_price")
        or public_data.get("target")
    )
    out["magnet_price"] = out["magnet_level"]
    out["sellside_liquidity"] = _safe_float(public_data.get("sellside_liquidity"))
    out["buyside_liquidity"] = _safe_float(public_data.get("buyside_liquidity"))
    out["zone_to_zone_target"] = _safe_float(public_data.get("zone_to_zone_target"))

    instruction_data = instruction if isinstance(instruction, dict) else {}
    zone = instruction_data.get("entry_zone")
    if isinstance(zone, dict):
        out["entry_zone_min"] = _safe_float(zone.get("min"))
        out["entry_zone_max"] = _safe_float(zone.get("max"))

    if out["magnet_level"] is None and isinstance(instruction_data.get("meta"), dict):
        meta = instruction_data.get("meta") or {}
        out["magnet_level"] = _safe_float(meta.get("magnet_level") or meta.get("magnet_price"))
        out["magnet_price"] = out["magnet_level"]
        if out["sellside_liquidity"] is None:
            out["sellside_liquidity"] = _safe_float(meta.get("sellside_liquidity"))
        if out["buyside_liquidity"] is None:
            out["buyside_liquidity"] = _safe_float(meta.get("buyside_liquidity"))
        if out["zone_to_zone_target"] is None:
            out["zone_to_zone_target"] = _safe_float(meta.get("zone_to_zone_target"))

    return out


def queue_autotrade_job_for_user(
    db: Session,
    *,
    user_id,
    symbol: str,
    strategy_name: str = DAILY_BIAS,
    volume: float | None = None,
    mode: str = "daily_bias",
) -> dict:
    symbol_value = symbol.strip().upper()

    enabled, reason, plan = is_autotrade_enabled_for_user_symbol(db, user_id=user_id, symbol=symbol_value)
    if not enabled:
        return _blocked(db, user_id=user_id, symbol=symbol_value, reason=reason, plan=plan)

    risk_settings = _get_or_create_user_risk_settings(db, user_id=user_id)
    allowed_symbols_cfg = (
        [str(s).strip().upper() for s in (risk_settings.allowed_symbols_json or []) if str(s).strip()]
        if isinstance(risk_settings.allowed_symbols_json, list)
        else []
    )
    if allowed_symbols_cfg and symbol_value not in set(allowed_symbols_cfg):
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="risk_settings_symbol_blocked",
            plan=plan,
            extra={"allowed_symbols_json": allowed_symbols_cfg},
        )

    try:
        validate_symbol_for_strategy(symbol=symbol_value, strategy_name=strategy_name, tier=plan)
    except StrategyMatrixError as exc:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason=f"strategy_matrix_{exc.reason}",
            plan=plan,
        )

    run = _latest_run_for_symbol(db, symbol_value)
    if not run:
        return _blocked(db, user_id=user_id, symbol=symbol_value, reason="oracle_run_missing", plan=plan)
    if run.status not in {"confirmed", "sent"}:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason=f"oracle_status_{run.status}",
            plan=plan,
            run_id=str(run.id),
        )

    public = _extract_public(run)
    final_dir = _final_direction_for_elite(run)
    if final_dir not in ACTIONABLE_DIRECTIONS:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="non_actionable_direction",
            plan=plan,
            run_id=str(run.id),
        )

    confirm_ok = bool(public.get("confirm_ok"))
    if not confirm_ok:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="confirm_not_ok",
            plan=plan,
            run_id=str(run.id),
        )
    if _confirm_tf(run) != "M15":
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="confirm_tf_not_m15",
            plan=plan,
            run_id=str(run.id),
        )
    if str(run.timeframe).upper() != "H1":
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="h1_direction_missing",
            plan=plan,
            run_id=str(run.id),
        )
    if str(run.bias).upper() != final_dir:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="h1_direction_conflict",
            plan=plan,
            run_id=str(run.id),
        )

    risk_reasons, risk_multiplier, risk_context = _risk_gate_result(run, risk_settings=risk_settings)
    if risk_reasons:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="risk_gate_blocked",
            plan=plan,
            run_id=str(run.id),
            extra={"risk_reasons": risk_reasons, "risk_context": risk_context},
        )

    trades_today = _trades_today(db, user_id=user_id)
    max_trades_day = int(risk_settings.max_trades_day or settings.AUTOTRADE_MAX_TRADES_PER_DAY)
    if trades_today >= max_trades_day:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="max_trades_per_day",
            plan=plan,
            run_id=str(run.id),
            extra={"trades_today": trades_today, "max_trades_day": max_trades_day},
        )

    daily_loss = _daily_loss_today(db, user_id=user_id)
    max_daily_loss = float(risk_settings.max_daily_loss or 0.0)
    if max_daily_loss > 0 and daily_loss >= max_daily_loss:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="max_daily_loss_exceeded",
            plan=plan,
            run_id=str(run.id),
            extra={"daily_loss": daily_loss, "max_daily_loss": max_daily_loss},
        )

    max_open_trades = int(risk_settings.max_open_trades or settings.AUTOTRADE_MAX_OPEN_TRADES_PER_SYMBOL)
    open_total = _open_positions_total(db, user_id=user_id)
    if open_total >= max_open_trades:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="max_open_trades_exceeded",
            plan=plan,
            run_id=str(run.id),
            extra={"open_trades": open_total, "max_open_trades": max_open_trades},
        )

    open_for_symbol = _open_positions_for_symbol(db, user_id=user_id, symbol=symbol_value)
    if open_for_symbol >= int(settings.AUTOTRADE_MAX_OPEN_TRADES_PER_SYMBOL):
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="max_open_trades_per_symbol",
            plan=plan,
            run_id=str(run.id),
            extra={"open_for_symbol": open_for_symbol},
        )

    if volume is not None:
        requested_volume = float(volume)
    elif str(risk_settings.risk_mode).lower() == "fixed":
        requested_volume = float(risk_settings.risk_value or settings.AUTOTRADE_DEFAULT_VOLUME)
    else:
        requested_volume = float(settings.AUTOTRADE_DEFAULT_VOLUME)

    requested_volume = round(requested_volume * float(risk_multiplier), 4)
    if requested_volume <= 0:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="invalid_volume",
            plan=plan,
            run_id=str(run.id),
        )
    max_lot = min(float(risk_settings.max_lot or settings.AUTOTRADE_MAX_VOLUME), float(settings.AUTOTRADE_MAX_VOLUME))
    if requested_volume > max_lot:
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="volume_cap_exceeded",
            plan=plan,
            run_id=str(run.id),
            extra={"volume": requested_volume, "max_lot": max_lot},
        )

    instruction = build_execution_instruction(
        db,
        symbol=symbol_value,
        target_tier="elite",
        requested_session="auto",
    )
    if not bool(instruction.get("enabled")):
        reasons = (instruction.get("meta") or {}).get("reasons") or []
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="oracle_exec_blocked",
            plan=plan,
            run_id=str(run.id),
            extra={"exec_reasons": reasons},
        )

    side = str(instruction.get("side", "")).upper()
    if side != ACTIONABLE_DIRECTIONS.get(final_dir):
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="direction_mismatch",
            plan=plan,
            run_id=str(run.id),
        )

    entry_type = str(instruction.get("entry_zone", {}).get("order_type", "MARKET")).upper()
    entry_type_value = "LIMIT" if entry_type.startswith("LIMIT") else "MARKET"
    entry_price = _safe_float(instruction.get("entry_price"))
    if entry_price is None:
        zone = instruction.get("entry_zone") if isinstance(instruction.get("entry_zone"), dict) else {}
        low = _safe_float(zone.get("min"))
        high = _safe_float(zone.get("max"))
        if low is not None and high is not None:
            entry_price = round((low + high) / 2.0, 6)
    sl = _safe_float(instruction.get("sl"))
    tp = _safe_float(instruction.get("tp1") or instruction.get("tp"))
    expires_at = _parse_iso(instruction.get("expires_at_utc")) or (datetime.now(timezone.utc) + timedelta(minutes=15))
    liquidity_context = _build_liquidity_context(public=public, instruction=instruction)
    validation = validate_trade_payload(
        direction=side,
        entry=entry_price,
        sl=sl,
        tp=tp,
        daily_permission=public.get("daily_permission") or final_dir,
        require_h1_confirmation=_strategy_requires_h1_confirmation(strategy_name),
        h1_confirm_ok=bool(public.get("h1_confirm_ok") if "h1_confirm_ok" in public else public.get("confirm_ok")),
        require_liquidity_context=_strategy_requires_liquidity_context(strategy_name),
        liquidity_context=liquidity_context,
    )
    if not validation.ok:
        logger.warning(
            "TRADE BLOCKED - %s user_id=%s symbol=%s run_id=%s phase=autotrade_queue",
            validation.reason,
            user_id,
            symbol_value,
            run.id,
        )
        return _blocked(
            db,
            user_id=user_id,
            symbol=symbol_value,
            reason="trade_validation_failed",
            plan=plan,
            run_id=str(run.id),
            extra={"validation_reason": validation.reason, "validation_details": validation.details},
        )

    reason_json = {
        "mode": mode,
        "strategy_name": strategy_name,
        "run_id": str(run.id),
        "instruction": instruction,
        "final_direction": final_dir,
        "risk_multiplier": risk_multiplier,
        "risk_context": risk_context,
    }
    job = TradeJob(
        user_id=user_id,
        run_id=run.id,
        symbol=symbol_value,
        side=side,
        volume=requested_volume,
        entry_type=entry_type_value,
        entry_price=entry_price,
        sl=sl,
        tp=tp,
        reason_json=reason_json,
        status="queued",
        expires_at=expires_at,
    )
    db.add(job)
    db.flush()

    try:
        _send_trade_update(
            db,
            user_id=user_id,
            symbol=symbol_value,
            title="Trade Planned",
            body=(
                f"Symbol: {symbol_value}\n"
                f"Side: {side}\n"
                f"Volume: {requested_volume}\n"
                f"Entry Type: {entry_type_value}\n"
                f"SL: {sl if sl is not None else '-'}\n"
                f"TP: {tp if tp is not None else '-'}\n"
                f"Job ID: {job.id}"
            ),
        )
    except Exception:
        # Telegram delivery should not block queue creation.
        pass

    _record_audit_event(
        db,
        user_id=user_id,
        symbol=symbol_value,
        action="autotrade.queue_precheck",
        allowed=True,
        reason_json={
            "reason": "queued",
            "plan": plan,
            "run_id": str(run.id),
            "risk_multiplier": risk_multiplier,
        },
    )

    return {
        "ok": True,
        "reason": "queued",
        "job_id": str(job.id),
        "run_id": str(run.id),
        "symbol": symbol_value,
        "side": side,
        "volume": requested_volume,
        "entry_type": entry_type_value,
        "plan": plan,
        "risk_multiplier": risk_multiplier,
    }


def queue_autotrade_jobs_for_symbol(
    db: Session,
    *,
    symbol: str,
    strategy_name: str = DAILY_BIAS,
    volume: float | None = None,
    user_id=None,
    mode: str = "daily_bias",
) -> dict:
    symbol_value = symbol.strip().upper()
    users_query = (
        db.query(User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .filter(User.role == "admin")
        .filter(Subscription.status.in_(ACTIVE_SUB_STATUSES))
    )
    admin_email = (settings.AUTOTRADE_ADMIN_EMAIL or "").strip().lower()
    if admin_email:
        users_query = users_query.filter(func.lower(User.email) == admin_email)
    if user_id:
        users_query = users_query.filter(User.id == user_id)
    user_ids = [row[0] for row in users_query.all()]

    created: list[dict] = []
    blocked: list[dict] = []
    reasons = Counter()

    for uid in user_ids:
        result = queue_autotrade_job_for_user(
            db,
            user_id=uid,
            symbol=symbol_value,
            strategy_name=strategy_name,
            volume=volume,
            mode=mode,
        )
        if result.get("ok"):
            created.append({"user_id": str(uid), **result})
        else:
            reason = str(result.get("reason", "blocked"))
            reasons[reason] += 1
            blocked.append({"user_id": str(uid), **result})

    return {
        "ok": True,
        "symbol": symbol_value,
        "created_count": len(created),
        "blocked_count": len(blocked),
        "blocked_reasons": dict(reasons),
        "created": created,
        "blocked": blocked,
    }


def next_trade_job_for_runner(db: Session, *, runner_id: str) -> dict | None:
    now_utc = datetime.now(timezone.utc)
    jobs = (
        db.query(TradeJob)
        .filter(TradeJob.status.in_(JOB_DISPATCHABLE_STATUSES))
        .order_by(TradeJob.created_at.asc())
        .limit(50)
        .all()
    )
    for job in jobs:
        if job.expires_at and _as_utc(job.expires_at) <= now_utc:
            job.status = "canceled"
            payload = job.reason_json if isinstance(job.reason_json, dict) else {}
            payload["runner_block_reason"] = "job_expired"
            job.reason_json = payload
            db.add(job)
            _record_audit_event(
                db,
                user_id=job.user_id,
                symbol=job.symbol,
                action="autotrade.runner_dispatch",
                allowed=False,
                reason_json={"reason": "job_expired", "job_id": str(job.id)},
            )
            continue

        enabled, reason, _plan = is_autotrade_enabled_for_user_symbol(db, user_id=job.user_id, symbol=job.symbol)
        if not enabled:
            job.status = "blocked"
            payload = job.reason_json if isinstance(job.reason_json, dict) else {}
            payload["runner_block_reason"] = reason
            job.reason_json = payload
            db.add(job)
            _record_audit_event(
                db,
                user_id=job.user_id,
                symbol=job.symbol,
                action="autotrade.runner_dispatch",
                allowed=False,
                reason_json={"reason": reason, "job_id": str(job.id)},
            )
            continue

        job.status = "dispatched"
        job.broker_runner_id = runner_id
        job.sent_to_runner_at = now_utc
        db.add(job)
        _record_audit_event(
            db,
            user_id=job.user_id,
            symbol=job.symbol,
            action="autotrade.runner_dispatch",
            allowed=True,
            reason_json={"reason": "dispatched", "job_id": str(job.id), "runner_id": runner_id},
        )
        db.flush()
        return _build_job_payload(job)
    return None


def _upsert_trade_exec(
    db: Session,
    *,
    job_id,
    status: str,
    broker_ticket: str | None,
    filled_price: float | None,
    error: str | None,
) -> TradeExec:
    row = db.query(TradeExec).filter(TradeExec.job_id == job_id).first()
    if not row:
        row = TradeExec(job_id=job_id, status=status)
        db.add(row)
    row.status = status
    row.broker_ticket = broker_ticket
    row.filled_price = filled_price
    row.error = error
    if status in {"filled", "failed", "canceled"}:
        row.completed_at = datetime.now(timezone.utc)
    db.add(row)
    db.flush()
    return row


def _maybe_create_or_update_trade_from_fill(db: Session, *, job: TradeJob, broker_ticket: str | None, filled_price: float | None) -> bool:
    if not broker_ticket:
        return False
    fill_price = float(filled_price if filled_price is not None else (job.entry_price or 0.0))
    tp2 = None
    reason_payload = job.reason_json if isinstance(job.reason_json, dict) else {}
    instruction = {}
    if isinstance(job.reason_json, dict):
        instruction = reason_payload.get("instruction")
        if isinstance(instruction, dict):
            tp2 = _safe_float(instruction.get("tp2"))
        else:
            instruction = {}

    run_public: dict = {}
    run_id_raw = reason_payload.get("run_id")
    if run_id_raw:
        try:
            run_id = UUID(str(run_id_raw))
            run_row = db.query(OracleRun).filter(OracleRun.id == run_id).first()
            if run_row is not None:
                run_public = _extract_public(run_row)
        except (TypeError, ValueError):
            run_public = {}

    strategy_name = str(reason_payload.get("strategy_name") or DAILY_BIAS).strip().upper()
    validation = validate_trade_payload(
        direction=job.side,
        entry=fill_price,
        sl=job.sl,
        tp=job.tp,
        daily_permission=run_public.get("daily_permission") or reason_payload.get("final_direction"),
        require_h1_confirmation=_strategy_requires_h1_confirmation(strategy_name),
        h1_confirm_ok=bool(run_public.get("h1_confirm_ok") if "h1_confirm_ok" in run_public else run_public.get("confirm_ok")),
        require_liquidity_context=_strategy_requires_liquidity_context(strategy_name),
        liquidity_context=_build_liquidity_context(public=run_public, instruction=instruction),
    )
    if not validation.ok:
        logger.warning(
            "TRADE BLOCKED - %s user_id=%s symbol=%s job_id=%s phase=trade_fill",
            validation.reason,
            job.user_id,
            job.symbol,
            job.id,
        )
        _record_audit_event(
            db,
            user_id=job.user_id,
            symbol=job.symbol,
            action="autotrade.fill_validation",
            allowed=False,
            reason_json={
                "reason": "trade_validation_failed",
                "job_id": str(job.id),
                "validation_reason": validation.reason,
                "validation_details": validation.details,
            },
        )
        return False

    try:
        pack = create_trade_for_signal(
            db,
            user_id=job.user_id,
            symbol=job.symbol,
            tier="elite",
            direction=job.side,
            entry=fill_price,
            sl=float(job.sl or fill_price),
            tp1=float(job.tp or fill_price),
            tp2=tp2,
            reasons=["Execution accepted by MT5 runner.", f"Broker ticket {broker_ticket} was opened."],
            opened_at_utc=datetime.now(timezone.utc),
            daily_permission=run_public.get("daily_permission") or reason_payload.get("final_direction"),
            require_h1_confirmation=_strategy_requires_h1_confirmation(strategy_name),
            h1_confirm_ok=bool(run_public.get("h1_confirm_ok") if "h1_confirm_ok" in run_public else run_public.get("confirm_ok")),
            require_liquidity_context=_strategy_requires_liquidity_context(strategy_name),
            liquidity_context=_build_liquidity_context(public=run_public, instruction=instruction),
            strategy_name=strategy_name,
        )
    except ValueError as exc:
        logger.warning(
            "TRADE BLOCKED - %s user_id=%s symbol=%s job_id=%s phase=trade_fill_create",
            str(exc),
            job.user_id,
            job.symbol,
            job.id,
        )
        return False
    trade = pack.trade
    reason_json = trade.reason_json if isinstance(trade.reason_json, dict) else {}
    reason_json["broker_ticket"] = broker_ticket
    reason_json["trade_job_id"] = str(job.id)
    trade.reason_json = reason_json
    db.add(trade)
    return True


def _upsert_position_state(
    db: Session,
    *,
    user_id,
    symbol: str,
    ticket: str,
    side: str,
    volume: float,
    entry: float,
    sl: float | None,
    tp: float | None,
    pnl: float | None,
) -> PositionState:
    row = (
        db.query(PositionState)
        .filter(PositionState.user_id == user_id, PositionState.ticket == str(ticket))
        .first()
    )
    if not row:
        row = PositionState(
            user_id=user_id,
            symbol=symbol,
            ticket=str(ticket),
            side=side,
            volume=float(volume),
            entry=float(entry),
        )
        db.add(row)
    row.symbol = symbol
    row.side = side
    row.volume = float(volume)
    row.entry = float(entry)
    row.sl = sl
    row.tp = tp
    row.pnl = pnl
    db.add(row)
    db.flush()
    return row


def submit_trade_job_result(
    db: Session,
    *,
    job_id: UUID,
    status: str,
    broker_ticket: str | None = None,
    filled_price: float | None = None,
    error: str | None = None,
) -> dict:
    job = db.query(TradeJob).filter(TradeJob.id == job_id).first()
    if not job:
        raise ValueError("trade_job_not_found")

    status_value = status.strip().lower()
    if status_value not in {"filled", "failed", "canceled"}:
        raise ValueError("invalid_job_result_status")

    job.status = status_value
    db.add(job)
    exec_row = _upsert_trade_exec(
        db,
        job_id=job.id,
        status=status_value,
        broker_ticket=broker_ticket,
        filled_price=filled_price,
        error=error,
    )

    if status_value == "filled":
        ticket = str(broker_ticket or "")
        if ticket:
            _upsert_position_state(
                db,
                user_id=job.user_id,
                symbol=job.symbol,
                ticket=ticket,
                side=job.side,
                volume=float(job.volume),
                entry=float(filled_price if filled_price is not None else (job.entry_price or 0.0)),
                sl=_safe_float(job.sl),
                tp=_safe_float(job.tp),
                pnl=None,
            )
        trade_created = _maybe_create_or_update_trade_from_fill(
            db,
            job=job,
            broker_ticket=broker_ticket,
            filled_price=filled_price,
        )
        if trade_created:
            try:
                _send_trade_update(
                    db,
                    user_id=job.user_id,
                    symbol=job.symbol,
                    title="Trade Filled",
                    body=(
                        f"Symbol: {job.symbol}\n"
                        f"Side: {job.side}\n"
                        f"Ticket: {broker_ticket or '-'}\n"
                        f"Filled Price: {filled_price if filled_price is not None else '-'}\n"
                        f"Job ID: {job.id}"
                    ),
                )
            except Exception:
                pass
    elif status_value in {"failed", "canceled"}:
        try:
            _send_trade_update(
                db,
                user_id=job.user_id,
                symbol=job.symbol,
                title="Trade Canceled" if status_value == "canceled" else "Trade Rejected",
                body=(
                    f"Symbol: {job.symbol}\n"
                    f"Job ID: {job.id}\n"
                    f"Reason: {error or status_value}"
                ),
            )
        except Exception:
            pass

    log_audit(
        db,
        action="runner.job.result",
        user_id=job.user_id,
        meta={
            "job_id": str(job.id),
            "status": status_value,
            "broker_ticket": broker_ticket,
            "filled_price": filled_price,
            "error": error,
            "exec_id": str(exec_row.id),
        },
    )

    return {
        "ok": True,
        "job_id": str(job.id),
        "status": status_value,
        "broker_ticket": broker_ticket,
        "filled_price": filled_price,
    }


def sync_positions(
    db: Session,
    *,
    rows: list[dict],
) -> dict:
    now_utc = datetime.now(timezone.utc)
    upserted = 0
    closed = 0
    ignored = 0

    for row in rows:
        user_id_raw = row.get("user_id")
        try:
            user_id = UUID(str(user_id_raw))
        except (TypeError, ValueError):
            ignored += 1
            continue

        symbol = str(row.get("symbol", "")).strip().upper()
        ticket = str(row.get("ticket", "")).strip()
        side = str(row.get("side", "")).strip().upper()
        status = str(row.get("status", "OPEN")).strip().upper()
        if not symbol or not ticket or side not in {"BUY", "SELL"}:
            ignored += 1
            continue

        if status in {"TP", "TP1", "TP2", "SL", "CLOSED"}:
            existing = (
                db.query(PositionState)
                .filter(PositionState.user_id == user_id, PositionState.ticket == ticket)
                .first()
            )
            if existing:
                db.delete(existing)
            closed += 1

            reason = str(row.get("reason") or status)
            title = "TP Hit" if status in {"TP", "TP1", "TP2"} else "SL Hit" if status == "SL" else "Trade Closed"
            trade = (
                db.query(Trade)
                .filter(Trade.user_id == user_id, Trade.symbol == symbol, Trade.status.in_(["OPEN", "TP1"]))
                .order_by(Trade.opened_at.desc())
                .first()
            )
            validation_context: dict[str, Any] = {}
            if trade and isinstance(trade.reason_json, dict):
                validation_context = trade.reason_json.get("validation_context") if isinstance(trade.reason_json.get("validation_context"), dict) else {}
            validation = validate_trade_payload(
                direction=(trade.direction if trade else side),
                entry=(trade.entry if trade else row.get("entry")),
                sl=(trade.sl if trade else row.get("sl")),
                tp=((trade.tp1 if trade and trade.tp1 is not None else (trade.tp2 if trade else row.get("tp")))),
                daily_permission=validation_context.get("daily_permission"),
                require_h1_confirmation=bool(validation_context.get("require_h1_confirmation")),
                h1_confirm_ok=validation_context.get("h1_confirm_ok"),
                require_liquidity_context=bool(validation_context.get("require_liquidity_context")),
                liquidity_context=validation_context.get("liquidity_context"),
            )
            if not validation.ok:
                logger.warning(
                    "TRADE BLOCKED - %s user_id=%s symbol=%s ticket=%s phase=trade_update_sync",
                    validation.reason,
                    user_id,
                    symbol,
                    ticket,
                )
                _record_audit_event(
                    db,
                    user_id=user_id,
                    symbol=symbol,
                    action="autotrade.positions_sync_validation",
                    allowed=False,
                    reason_json={
                        "reason": "trade_validation_failed",
                        "ticket": ticket,
                        "status": status,
                        "validation_reason": validation.reason,
                        "validation_details": validation.details,
                    },
                )
                continue

            try:
                _send_trade_update(
                    db,
                    user_id=user_id,
                    symbol=symbol,
                    title=title,
                    body=(
                        f"Ticket: {ticket}\n"
                        f"Outcome: {status}\n"
                        f"Timestamp: {format_london(now_utc)}\n"
                        f"Reason: {reason}"
                    ),
                )
            except Exception:
                pass

            if trade:
                if status in {"TP", "TP1", "TP2"}:
                    trade.status = "TP2" if status in {"TP2", "TP"} else "TP1"
                    trade.result = "WIN" if trade.status == "TP2" else None
                    record_event = "TP2" if trade.status == "TP2" else "TP1"
                elif status == "SL":
                    trade.status = "SL"
                    trade.result = "LOSS"
                    record_event = "SL"
                else:
                    trade.status = "CLOSED"
                    record_event = "CLOSE"
                trade.closed_at = now_utc
                db.add(trade)
                if not (
                    db.query(TradeEvent.id)
                    .filter(TradeEvent.trade_id == trade.id, TradeEvent.event_type == record_event)
                    .first()
                ):
                    db.add(
                        TradeEvent(
                            trade_id=trade.id,
                            event_type=record_event,
                            price=_safe_float(row.get("price")),
                            note=str(row.get("reason") or status),
                        )
                    )
            continue

        entry = _safe_float(row.get("entry"))
        volume = _safe_float(row.get("volume"))
        if entry is None or volume is None:
            ignored += 1
            continue
        _upsert_position_state(
            db,
            user_id=user_id,
            symbol=symbol,
            ticket=ticket,
            side=side,
            volume=volume,
            entry=entry,
            sl=_safe_float(row.get("sl")),
            tp=_safe_float(row.get("tp")),
            pnl=_safe_float(row.get("pnl")),
        )
        upserted += 1

    log_audit(
        db,
        action="runner.positions.sync",
        meta={"upserted": upserted, "closed": closed, "ignored": ignored},
    )
    return {"ok": True, "upserted": upserted, "closed": closed, "ignored": ignored}
