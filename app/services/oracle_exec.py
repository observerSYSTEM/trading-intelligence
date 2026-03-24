from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.symbols import allowed_symbols_for_plan, normalize_plan
from app.db.models import MT5Candle, OracleRun

VALID_ALLOWED_DIRECTIONS = {"BUY_ONLY", "SELL_ONLY"}
SIDE_MAP = {"BUY_ONLY": "BUY", "SELL_ONLY": "SELL"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_session(value: str | None) -> str:
    raw = (value or "auto").strip().lower()
    mapping = {
        "auto": "auto",
        "any": "any",
        "london": "london",
        "ny": "newyork",
        "newyork": "newyork",
        "new_york": "newyork",
    }
    return mapping.get(raw, "auto")


def _active_session(now_uk: datetime) -> str:
    hour = now_uk.hour
    if int(settings.ORACLE_EXEC_LONDON_START_HOUR) <= hour < int(settings.ORACLE_EXEC_LONDON_END_HOUR):
        return "london"
    if int(settings.ORACLE_EXEC_NEWYORK_START_HOUR) <= hour < int(settings.ORACLE_EXEC_NEWYORK_END_HOUR):
        return "newyork"
    return "closed"


def _session_allowed(requested: str, active: str) -> bool:
    if active == "closed":
        return bool(settings.ORACLE_EXEC_ALLOW_OFF_SESSION)
    if requested in {"auto", "any"}:
        return True
    return requested == active


def _price_precision(symbol: str) -> int:
    if symbol.startswith("XAU"):
        return 2
    if symbol.endswith("JPY"):
        return 3
    return 5


def _latest_candle(db: Session, *, symbol: str, timeframe: str) -> MT5Candle | None:
    return (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )


def _latest_candles(db: Session, *, symbol: str, timeframe: str, limit: int) -> list[MT5Candle]:
    rows = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return rows


def _atr_h1(db: Session, *, symbol: str, period: int = 14) -> float | None:
    candles = _latest_candles(db, symbol=symbol, timeframe="H1", limit=max(period + 2, 20))
    if len(candles) < period + 1:
        return None
    true_ranges: list[float] = []
    for idx in range(1, len(candles)):
        cur = candles[idx]
        prev = candles[idx - 1]
        tr = max(
            float(cur.high) - float(cur.low),
            abs(float(cur.high) - float(prev.close)),
            abs(float(cur.low) - float(prev.close)),
        )
        true_ranges.append(tr)
    window = true_ranges[-period:]
    if not window:
        return None
    return float(sum(window) / len(window))


def _clamp_ttl(ttl_seconds: int | None) -> int:
    base = int(ttl_seconds or settings.ORACLE_EXEC_DEFAULT_TTL_SECONDS)
    low = int(settings.ORACLE_EXEC_MIN_TTL_SECONDS)
    high = int(settings.ORACLE_EXEC_MAX_TTL_SECONDS)
    return max(low, min(high, base))


def _blank_instruction(
    *,
    symbol: str,
    side: str,
    created_at_utc: datetime,
    expires_at_utc: datetime,
    run: OracleRun | None,
    permission_layers: dict,
    risk_parameters: dict,
    comment: str,
    reasons: list[str],
    requested_session: str,
    active_session: str,
    target_tier: str,
) -> dict:
    return {
        "schema_version": "1.0",
        "instruction_id": str(uuid.uuid4()),
        "enabled": False,
        "created_at_utc": created_at_utc.isoformat(),
        "expires_at_utc": expires_at_utc.isoformat(),
        "symbol": symbol,
        "side": side,
        "entry_zone": {"min": None, "max": None, "order_type": "LIMIT_ZONE"},
        "sl": None,
        "tp1": None,
        "tp2": None,
        "permission_layers": permission_layers,
        "risk_parameters": risk_parameters,
        "comment": comment,
        "meta": {
            "snapshot_run_id": str(run.id) if run else None,
            "snapshot_as_of_utc": _as_utc(run.as_of_utc).isoformat() if run else None,
            "snapshot_status": run.status if run else None,
            "requested_session": requested_session,
            "active_session": active_session,
            "target_tier": target_tier,
            "reasons": reasons,
        },
    }


def build_execution_instruction(
    db: Session,
    *,
    symbol: str,
    target_tier: str = "elite",
    requested_session: str = "auto",
    ttl_seconds: int | None = None,
) -> dict:
    symbol_value = (symbol or "XAUUSD").strip().upper()
    tier = normalize_plan(target_tier)
    session_value = _normalize_session(requested_session)
    now_utc = datetime.now(timezone.utc)
    now_uk = _as_utc(now_utc).astimezone(UK_TZ)
    active_session = _active_session(now_uk)
    ttl = _clamp_ttl(ttl_seconds)
    expires_at_utc = now_utc + timedelta(seconds=ttl)

    run = (
        db.query(OracleRun)
        .filter(OracleRun.symbol == symbol_value)
        .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
        .first()
    )

    public = run.public_json if run and isinstance(run.public_json, dict) else {}
    quarterly_permission = str(public.get("quarterly_bias", "BOTH")).upper()
    daily_bias = str(public.get("daily_bias_raw", run.bias if run else "NO_TRADE")).upper()
    final_allowed = str(
        public.get("allowed_direction_final_soft", public.get("final_allowed_elite", run.bias if run else "NO_TRADE"))
    ).upper()
    confirm_ok = bool(public.get("confirm_ok", False))
    permission_alignment = str(public.get("permission_alignment", "NEUTRAL")).upper()
    volume_state = str(public.get("volume_state", "normal")).lower()
    atr_h1 = _safe_float(public.get("atr_h1"))
    if atr_h1 is None or atr_h1 <= 0:
        atr_h1 = _atr_h1(db, symbol=symbol_value)

    risk_parameters = {
        "max_risk_percent": float(settings.ORACLE_EXEC_MAX_RISK_PERCENT),
        "max_positions": int(settings.ORACLE_EXEC_MAX_POSITIONS),
        "max_spread_points": int(settings.ORACLE_EXEC_MAX_SPREAD_POINTS),
        "max_risk_points": float(settings.ORACLE_EXEC_MAX_RISK_POINTS),
        "tp1_r_mult": float(settings.ORACLE_EXEC_TP1_R_MULT),
        "tp2_r_mult": float(settings.ORACLE_EXEC_TP2_R_MULT),
    }
    permission_layers = {
        "quarterly_permission": quarterly_permission,
        "daily_bias": daily_bias,
        "m15_confirm_ok": confirm_ok,
        "volume_state": volume_state,
        "atr_h1": atr_h1,
        "alignment": permission_alignment,
        "session_window": active_session,
        "tier_gate": "elite_only",
    }

    reasons: list[str] = []
    if run is None:
        reasons.append("oracle_snapshot_missing")

    if tier != "elite":
        reasons.append("elite_only")

    if symbol_value not in allowed_symbols_for_plan("elite"):
        reasons.append("symbol_not_allowed_for_elite")

    if not _session_allowed(session_value, active_session):
        reasons.append("outside_trading_window")

    if not confirm_ok:
        reasons.append("m15_confirmation_failed")

    if final_allowed not in VALID_ALLOWED_DIRECTIONS:
        reasons.append("final_direction_not_actionable")

    if quarterly_permission in VALID_ALLOWED_DIRECTIONS and final_allowed in VALID_ALLOWED_DIRECTIONS:
        if final_allowed != quarterly_permission:
            reasons.append("quarterly_permission_block")

    if daily_bias in VALID_ALLOWED_DIRECTIONS and final_allowed in VALID_ALLOWED_DIRECTIONS:
        if final_allowed != daily_bias:
            reasons.append("daily_bias_misaligned")

    if permission_alignment == "CONFLICT":
        reasons.append("permission_conflict")

    if volume_state == "low":
        reasons.append("volume_too_low")

    atr_min = float(settings.ORACLE_ATR_H1_MIN)
    atr_max = float(settings.ORACLE_ATR_H1_MAX)
    if atr_h1 is None:
        reasons.append("atr_missing")
    elif atr_h1 < atr_min or atr_h1 > atr_max:
        reasons.append("atr_out_of_bounds")

    side = SIDE_MAP.get(final_allowed, "NONE")
    if reasons:
        return _blank_instruction(
            symbol=symbol_value,
            side=side,
            created_at_utc=now_utc,
            expires_at_utc=expires_at_utc,
            run=run,
            permission_layers=permission_layers,
            risk_parameters=risk_parameters,
            comment="Execution disabled by oracle_exec gates.",
            reasons=reasons,
            requested_session=session_value,
            active_session=active_session,
            target_tier=tier,
        )

    m15 = _latest_candle(db, symbol=symbol_value, timeframe="M15")
    h1 = _latest_candle(db, symbol=symbol_value, timeframe="H1")
    price_candle = m15 or h1
    if price_candle is None or atr_h1 is None:
        return _blank_instruction(
            symbol=symbol_value,
            side=side,
            created_at_utc=now_utc,
            expires_at_utc=expires_at_utc,
            run=run,
            permission_layers=permission_layers,
            risk_parameters=risk_parameters,
            comment="Execution disabled: missing market candle context.",
            reasons=["price_context_missing"],
            requested_session=session_value,
            active_session=active_session,
            target_tier=tier,
        )

    close_price = float(price_candle.close)
    level_atr = max(float(atr_h1), 1e-6)
    buffer = max(level_atr * float(settings.ORACLE_EXEC_ENTRY_BUFFER_ATR_MULT), 1e-6)
    sl_offset = max(level_atr * float(settings.ORACLE_EXEC_SL_ATR_MULT), buffer * 1.5)
    tp1_r = float(settings.ORACLE_EXEC_TP1_R_MULT)
    tp2_r = float(settings.ORACLE_EXEC_TP2_R_MULT)

    if side == "BUY":
        entry_min = close_price - buffer
        entry_max = close_price + (buffer * 0.25)
        entry_ref = (entry_min + entry_max) / 2.0
        sl = entry_min - sl_offset
        risk_points = entry_ref - sl
        tp1 = entry_ref + (risk_points * tp1_r)
        tp2 = entry_ref + (risk_points * tp2_r)
    else:
        entry_min = close_price - (buffer * 0.25)
        entry_max = close_price + buffer
        entry_ref = (entry_min + entry_max) / 2.0
        sl = entry_max + sl_offset
        risk_points = sl - entry_ref
        tp1 = entry_ref - (risk_points * tp1_r)
        tp2 = entry_ref - (risk_points * tp2_r)

    if risk_points <= 0 or risk_points > float(settings.ORACLE_EXEC_MAX_RISK_POINTS):
        return _blank_instruction(
            symbol=symbol_value,
            side=side,
            created_at_utc=now_utc,
            expires_at_utc=expires_at_utc,
            run=run,
            permission_layers=permission_layers,
            risk_parameters={**risk_parameters, "risk_points": risk_points},
            comment="Execution disabled: risk cap exceeded.",
            reasons=["risk_cap_exceeded"],
            requested_session=session_value,
            active_session=active_session,
            target_tier=tier,
        )

    precision = _price_precision(symbol_value)
    entry_zone = {
        "min": round(entry_min, precision),
        "max": round(entry_max, precision),
        "order_type": "LIMIT_ZONE",
    }
    sl_value = round(sl, precision)
    tp1_value = round(tp1, precision)
    tp2_value = round(tp2, precision)

    return {
        "schema_version": "1.0",
        "instruction_id": str(uuid.uuid4()),
        "enabled": True,
        "created_at_utc": now_utc.isoformat(),
        "expires_at_utc": expires_at_utc.isoformat(),
        "symbol": symbol_value,
        "side": side,
        "entry_zone": entry_zone,
        "sl": sl_value,
        "tp1": tp1_value,
        "tp2": tp2_value,
        "permission_layers": permission_layers,
        "risk_parameters": {
            **risk_parameters,
            "risk_points": round(risk_points, precision),
            "snapshot_confidence": run.confidence if run else None,
        },
        "comment": "Oracle-validated execution instruction. EA must execute/manage only.",
        "meta": {
            "snapshot_run_id": str(run.id) if run else None,
            "snapshot_as_of_utc": _as_utc(run.as_of_utc).isoformat() if run else None,
            "snapshot_status": run.status if run else None,
            "requested_session": session_value,
            "active_session": active_session,
            "target_tier": tier,
            "reasons": [],
        },
    }
