from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DailyPermissionSnapshot, OracleRun

logger = logging.getLogger(__name__)

_STATE_LOCK = Lock()
_RETRY_AFTER_MINUTES = 30

try:
    UK_TZ = ZoneInfo("Europe/London")
except ZoneInfoNotFoundError:
    UK_TZ = timezone.utc


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _state_path() -> Path:
    return _project_root() / "runtime" / "telegram_signal_alert_state.json"


def _read_state_unlocked() -> dict:
    path = _state_path()
    if not path.exists():
        return {"alerts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("alert_failed state_read path=%s", path)
        return {"alerts": {}}
    if isinstance(data, dict):
        alerts = data.get("alerts")
        if isinstance(alerts, dict):
            return {"alerts": alerts}
    return {"alerts": {}}


def _write_state_unlocked(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temp_path, path)


def _scope_key(*, symbol: str, timeframe: str, signal_type: str) -> str:
    return f"{symbol.strip().upper()}::{timeframe.strip().upper()}::{signal_type.strip().lower()}"


def _fingerprint_key(fingerprint: dict | str) -> str:
    if isinstance(fingerprint, dict):
        raw = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    else:
        raw = str(fingerprint or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _float_token(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{float(value):.5f}"


def _magnet_side_token(*, magnet: float | None, sellside: float | None, buyside: float | None) -> str | None:
    magnet_token = _float_token(magnet)
    if magnet_token is None:
        return None
    if magnet_token == _float_token(sellside):
        return "SELL"
    if magnet_token == _float_token(buyside):
        return "BUY"
    return None


def _format_price(value: float | None) -> str:
    if value is None:
        return "-"
    if abs(value) >= 100:
        return f"{value:.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}"
    return f"{value:.5f}"


def _format_confidence(value: float | None) -> str:
    if value is None:
        return "-"
    pct = value * 100.0 if float(value) <= 1.0 else float(value)
    return f"{pct:.1f}%"


def _format_london(value: datetime | None) -> str:
    current = _as_utc(value)
    local = current.astimezone(UK_TZ)
    return f"{local:%Y-%m-%d %H:%M:%S} London"


def _normalize_text(value: str | None, *, upper: bool = False) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.upper() if upper else text


def _humanize_reason(value: str | None, *, fallback: str = "-") -> str:
    text = _normalize_text(value)
    if not text:
        return fallback
    text = " ".join(text.replace("_", " ").split())
    if not text:
        return fallback
    return f"{text[0].upper()}{text[1:]}" if len(text) > 1 else text.upper()


def _format_permission_source(permission_source: str | None, permission_stage: str | None) -> str:
    source_value = _normalize_text(permission_source, upper=True)
    stage_value = _normalize_text(permission_stage, upper=True)
    if source_value and stage_value:
        return f"{source_value} / {stage_value}"
    return source_value or stage_value or "-"


def _derive_freshness(
    *,
    detected_at: datetime | None,
    stale_hint: bool = False,
    now_utc: datetime | None = None,
) -> str:
    if stale_hint:
        return "STALE"
    if detected_at is None:
        return "UNKNOWN"
    current_time = _as_utc(now_utc)
    detected_time = _as_utc(detected_at)
    age_minutes = max(int((current_time - detected_time).total_seconds() // 60), 0)
    if age_minutes >= 180:
        state = "STALE"
    elif age_minutes >= 30:
        state = "AGING"
    else:
        state = "FRESH"
    return f"{state} ({age_minutes}m old)"


def _derive_risk_state(
    *,
    final_allowed: str | None,
    h1_confirmation: str | None,
    permission_source: str | None,
    permission_stage: str | None,
    manipulation_level: str | None = None,
    permission_alignment: str | None = None,
    risk_banner: dict | None = None,
    risk_state: str | None = None,
) -> str:
    explicit = _normalize_text(risk_state, upper=True)
    if explicit:
        return explicit

    manipulation_value = _normalize_text(manipulation_level, upper=True)
    if manipulation_value == "HIGH":
        return "HIGH_MANIPULATION"

    alignment_value = _normalize_text(permission_alignment, upper=True)
    if alignment_value == "CONFLICT":
        return "CAUTION"

    banner = risk_banner if isinstance(risk_banner, dict) else {}
    if bool(banner.get("is_blueprint_day")) or bool(banner.get("volume_spike")):
        return "ELEVATED"

    final_allowed_value = _normalize_text(final_allowed, upper=True)
    h1_value = _normalize_text(h1_confirmation, upper=True)
    source_value = _normalize_text(permission_source, upper=True)
    stage_value = _normalize_text(permission_stage, upper=True)

    if final_allowed_value == "NO_TRADE":
        return "NO_TRADE"
    if (
        source_value == "LONDON_0801"
        and stage_value == "OFFICIAL"
        and final_allowed_value in {"BUY_ONLY", "SELL_ONLY"}
        and h1_value not in {"CONFIRMED", "NOT_CONFIRMED"}
    ):
        return "DAILY_LOCKED"
    if final_allowed_value in {"BUY_ONLY", "SELL_ONLY"} and h1_value == "CONFIRMED":
        return "ALIGNED"
    if final_allowed_value in {"BUY_ONLY", "SELL_ONLY"} and h1_value == "NOT_CONFIRMED":
        return "WAIT_CONFIRMATION"
    return "NEUTRAL"


def _build_premium_alert_message(
    *,
    title: str,
    symbol: str,
    detected_at: datetime | None,
    permission_source: str | None,
    permission_stage: str | None,
    daily_permission: str | None,
    final_allowed: str | None,
    h1_confirmation: str | None,
    m15_opportunity: str | None,
    confidence: float | None,
    magnet: float | None,
    zone_target: float | None,
    sellside: float | None,
    buyside: float | None,
    risk_state: str | None,
    freshness: str | None,
    reason: str | None,
) -> str:
    return "\n".join(
        [
            "TRADING INTELLIGENCE ALERT",
            title.strip(),
            "",
            f"Symbol: {symbol.strip().upper()}",
            f"London Time: {_format_london(detected_at)}",
            f"Permission Source/Stage: {_format_permission_source(permission_source, permission_stage)}",
            f"Daily Permission: {_normalize_text(daily_permission, upper=True) or '-'}",
            f"Final Allowed: {_normalize_text(final_allowed, upper=True) or '-'}",
            f"H1 Confirmation: {_normalize_text(h1_confirmation, upper=True) or '-'}",
            f"M15 Opportunity: {_normalize_text(m15_opportunity, upper=True) or '-'}",
            f"Confidence: {_format_confidence(confidence)}",
            "",
            f"Magnet: {_format_price(magnet)}",
            f"Zone Target: {_format_price(zone_target)}",
            f"Sellside Liquidity: {_format_price(sellside)}",
            f"Buyside Liquidity: {_format_price(buyside)}",
            "",
            f"Risk State: {_normalize_text(risk_state, upper=True) or '-'}",
            f"Freshness: {freshness or _derive_freshness(detected_at=detected_at)}",
            f"Reason: {_humanize_reason(reason)}",
        ]
    )


def should_send_alert(
    *,
    scope: str,
    fingerprint: dict | str,
    material_refresh: bool,
    now_utc: datetime | None = None,
) -> dict:
    dedupe_key = _fingerprint_key(fingerprint)
    current_time = _as_utc(now_utc)

    with _STATE_LOCK:
        state = _read_state_unlocked()
        alerts = state.setdefault("alerts", {})
        record = alerts.get(scope) if isinstance(alerts, dict) else None
        if not isinstance(record, dict):
            return {"send": True, "reason": "first_alert", "dedupe_key": dedupe_key, "scope": scope}

        last_key = str(record.get("dedupe_key") or "")
        last_sent_raw = str(record.get("sent_at") or "")
        last_sent_at: datetime | None = None
        if last_sent_raw:
            try:
                last_sent_at = _as_utc(datetime.fromisoformat(last_sent_raw))
            except ValueError:
                last_sent_at = None

        if last_key != dedupe_key:
            return {"send": True, "reason": "dedupe_key_changed", "dedupe_key": dedupe_key, "scope": scope}

        if last_sent_at is None:
            return {"send": True, "reason": "missing_timestamp", "dedupe_key": dedupe_key, "scope": scope}

        if material_refresh and current_time - last_sent_at >= timedelta(minutes=_RETRY_AFTER_MINUTES):
            return {"send": True, "reason": "refresh_after_30m", "dedupe_key": dedupe_key, "scope": scope}

        return {"send": False, "reason": "alert_skipped_duplicate", "dedupe_key": dedupe_key, "scope": scope}


def record_alert_sent(
    *,
    scope: str,
    fingerprint: dict | str,
    sent_at: datetime | None = None,
    payload: dict | None = None,
) -> None:
    current_time = _as_utc(sent_at)
    dedupe_key = _fingerprint_key(fingerprint)

    with _STATE_LOCK:
        state = _read_state_unlocked()
        alerts = state.setdefault("alerts", {})
        if not isinstance(alerts, dict):
            alerts = {}
            state["alerts"] = alerts
        alerts[scope] = {
            "dedupe_key": dedupe_key,
            "sent_at": current_time.isoformat(),
            "payload": payload or {},
        }
        _write_state_unlocked(state)


def build_signal_alert_dedupe_key(
    *,
    symbol: str,
    timeframe: str,
    bias: str | None,
    magnet: float | None,
    zone_target: float | None,
    signal_type: str,
) -> str:
    fingerprint = {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "bias": (bias or "").strip().upper(),
        "magnet": _float_token(magnet),
        "zone_target": _float_token(zone_target),
        "signal_type": signal_type.strip().lower(),
    }
    return _fingerprint_key(fingerprint)


def should_send_signal_alert(
    *,
    symbol: str,
    timeframe: str,
    bias: str | None,
    magnet: float | None,
    zone_target: float | None,
    signal_type: str,
    material_refresh: bool,
    now_utc: datetime | None = None,
) -> dict:
    scope = _scope_key(symbol=symbol, timeframe=timeframe, signal_type=signal_type)
    fingerprint = {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "bias": (bias or "").strip().upper(),
        "magnet": _float_token(magnet),
        "zone_target": _float_token(zone_target),
        "signal_type": signal_type.strip().lower(),
    }
    return should_send_alert(
        scope=scope,
        fingerprint=fingerprint,
        material_refresh=material_refresh,
        now_utc=now_utc,
    )


def record_signal_alert_sent(
    *,
    symbol: str,
    timeframe: str,
    bias: str | None,
    magnet: float | None,
    zone_target: float | None,
    signal_type: str,
    sent_at: datetime | None = None,
) -> None:
    scope = _scope_key(symbol=symbol, timeframe=timeframe, signal_type=signal_type)
    fingerprint = {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "bias": (bias or "").strip().upper(),
        "magnet": _float_token(magnet),
        "zone_target": _float_token(zone_target),
        "signal_type": signal_type.strip().lower(),
    }
    record_alert_sent(
        scope=scope,
        fingerprint=fingerprint,
        sent_at=sent_at,
        payload={
            "symbol": symbol.strip().upper(),
            "timeframe": timeframe.strip().upper(),
            "signal_type": signal_type.strip().lower(),
            "bias": (bias or "").strip().upper() or None,
        },
    )


def latest_oracle_alert_context(db: Session, *, symbol: str) -> dict:
    symbol_value = (symbol or "").strip().upper()
    context = {
        "daily_permission": None,
        "permission_stage": None,
        "permission_source": None,
        "final_allowed": None,
        "h1_confirmation": None,
        "m15_opportunity": None,
        "confidence": None,
        "reason": None,
        "risk_state": None,
        "freshness": None,
    }

    permission_row = (
        db.query(DailyPermissionSnapshot)
        .filter(DailyPermissionSnapshot.symbol == symbol_value)
        .order_by(DailyPermissionSnapshot.as_of_utc.desc(), DailyPermissionSnapshot.created_at.desc())
        .first()
    )
    if permission_row is not None:
        context["daily_permission"] = str(permission_row.daily_permission or "").strip().upper() or None
        context["permission_stage"] = str(permission_row.daily_permission_stage or "").strip().upper() or None
        context["permission_source"] = str(permission_row.permission_source or "").strip().upper() or None
        context["reason"] = str(permission_row.reason or "").strip() or None
        factors = permission_row.factors_json if isinstance(permission_row.factors_json, dict) else {}
        context["freshness"] = _derive_freshness(
            detected_at=permission_row.as_of_utc,
            stale_hint=bool(factors.get("missing_data")) or bool(factors.get("future_timestamp")),
        )

    latest_run = (
        db.query(OracleRun)
        .filter(OracleRun.symbol == symbol_value, OracleRun.timeframe == "M15")
        .order_by(OracleRun.as_of_utc.desc(), OracleRun.created_at.desc())
        .first()
    )
    if latest_run is not None:
        public = latest_run.public_json if isinstance(latest_run.public_json, dict) else {}
        context["final_allowed"] = (
            str(public.get("final_allowed_basic") or latest_run.bias or "").strip().upper() or None
        )
        context["h1_confirmation"] = "CONFIRMED" if bool(public.get("confirm_ok")) else "NOT_CONFIRMED"
        context["m15_opportunity"] = (
            str(public.get("opportunity_direction") or latest_run.bias or "").strip().upper() or None
        )
        try:
            context["confidence"] = float(latest_run.confidence) if latest_run.confidence is not None else None
        except Exception:
            context["confidence"] = None
        reason_value = str(public.get("c1") or "").strip()
        if reason_value:
            context["reason"] = reason_value
        context["risk_state"] = _derive_risk_state(
            final_allowed=context["final_allowed"],
            h1_confirmation=context["h1_confirmation"],
            permission_source=context["permission_source"],
            permission_stage=context["permission_stage"],
            manipulation_level=str(latest_run.manipulation_level or "").strip().upper() or None,
            permission_alignment=str(public.get("permission_alignment") or "").strip().upper() or None,
            risk_banner=public.get("risk_banner") if isinstance(public.get("risk_banner"), dict) else None,
        )
        context["freshness"] = _derive_freshness(detected_at=latest_run.as_of_utc)

    if not context["risk_state"]:
        context["risk_state"] = _derive_risk_state(
            final_allowed=context["final_allowed"] or context["daily_permission"],
            h1_confirmation=context["h1_confirmation"],
            permission_source=context["permission_source"],
            permission_stage=context["permission_stage"],
        )
    if not context["freshness"]:
        context["freshness"] = _derive_freshness(detected_at=permission_row.as_of_utc if permission_row is not None else None)

    return context


def maybe_send_daily_alignment_alert(
    *,
    symbol: str,
    detected_at: datetime | None,
    permission_source: str | None,
    permission_stage: str | None,
    daily_permission: str | None,
    final_allowed: str | None,
    h1_confirmation: str | None,
    m15_opportunity: str | None,
    confidence: float | None,
    reason: str | None,
    magnet: float | None,
    zone_target: float | None,
    sellside: float | None,
    buyside: float | None,
    material_refresh: bool,
    risk_state: str | None = None,
    freshness: str | None = None,
) -> dict:
    symbol_value = (symbol or "").strip().upper()
    detected_time = _as_utc(detected_at)
    permission_source_value = (permission_source or "").strip().upper()
    permission_stage_value = (permission_stage or "").strip().upper()
    daily_permission_value = (daily_permission or "").strip().upper()
    final_allowed_value = (final_allowed or "").strip().upper()
    if final_allowed_value not in {"BUY_ONLY", "SELL_ONLY"}:
        final_allowed_value = daily_permission_value if daily_permission_value in {"BUY_ONLY", "SELL_ONLY"} else ""
    h1_confirmation_value = (h1_confirmation or "").strip().upper()
    m15_opportunity_value = (m15_opportunity or "").strip().upper()
    reason_value = (reason or "").strip()

    if not (
        permission_source_value == "LONDON_0801"
        and permission_stage_value == "OFFICIAL"
        and final_allowed_value in {"BUY_ONLY", "SELL_ONLY"}
        and daily_permission_value in {"BUY_ONLY", "SELL_ONLY"}
    ):
        return {"status": "not_applicable", "reason": "alignment_incomplete"}

    fingerprint = {
        "symbol": symbol_value,
        "permission_source": permission_source_value,
        "permission_stage": permission_stage_value,
        "daily_permission": daily_permission_value,
        "final_allowed": final_allowed_value,
        "date_uk": detected_time.astimezone(UK_TZ).date().isoformat(),
    }
    scope = f"{symbol_value}::daily_alignment"
    dedupe_check = should_send_alert(
        scope=scope,
        fingerprint=fingerprint,
        material_refresh=False,
        now_utc=detected_time,
    )
    if not dedupe_check.get("send"):
        return {"status": "alert_skipped_duplicate", **dedupe_check}

    message = _build_premium_alert_message(
        title="DAILY ALIGNMENT CONFIRMED",
        symbol=symbol_value,
        detected_at=detected_time,
        permission_source=permission_source_value,
        permission_stage=permission_stage_value,
        daily_permission=daily_permission_value,
        final_allowed=final_allowed_value,
        h1_confirmation=h1_confirmation_value,
        m15_opportunity=m15_opportunity_value,
        confidence=confidence,
        magnet=magnet,
        zone_target=zone_target,
        sellside=sellside,
        buyside=buyside,
        risk_state=_derive_risk_state(
            final_allowed=final_allowed_value,
            h1_confirmation=h1_confirmation_value,
            permission_source=permission_source_value,
            permission_stage=permission_stage_value,
            risk_state=risk_state,
        ),
        freshness=freshness or _derive_freshness(detected_at=detected_time),
        reason=reason_value or "08:01 daily alignment confirmed.",
    )
    if not send_telegram_signal(message):
        return {"status": "alert_failed", **dedupe_check}

    record_alert_sent(scope=scope, fingerprint=fingerprint, sent_at=detected_time, payload=fingerprint)
    return {"status": "alert_sent", **dedupe_check}


def maybe_send_liquidity_target_alert(
    *,
    symbol: str,
    as_of_utc: datetime | None,
    reason: str | None,
    magnet: float | None,
    zone_target: float | None,
    sellside: float | None,
    buyside: float | None,
    daily_permission: str | None = None,
    permission_source: str | None = None,
    permission_stage: str | None = None,
    final_allowed: str | None = None,
    h1_confirmation: str | None = None,
    m15_opportunity: str | None = None,
    confidence: float | None = None,
    risk_state: str | None = None,
    freshness: str | None = None,
) -> dict:
    symbol_value = (symbol or "").strip().upper()
    detected_time = _as_utc(as_of_utc)
    reason_value = (reason or "").strip()
    permission_source_value = (permission_source or "").strip().upper() or None
    permission_stage_value = (permission_stage or "").strip().upper() or None
    final_allowed_value = (final_allowed or "").strip().upper() or None
    h1_confirmation_value = (h1_confirmation or "").strip().upper() or None
    m15_opportunity_value = (m15_opportunity or "").strip().upper() or None

    if magnet is None and zone_target is None and sellside is None and buyside is None:
        return {"status": "not_applicable", "reason": "missing_liquidity_values"}

    fingerprint = {
        "symbol": symbol_value,
        "magnet_side": _magnet_side_token(magnet=magnet, sellside=sellside, buyside=buyside),
        "magnet": _float_token(magnet),
        "zone_target": _float_token(zone_target),
    }
    scope = f"{symbol_value}::liquidity_target"
    dedupe_check = should_send_alert(
        scope=scope,
        fingerprint=fingerprint,
        material_refresh=False,
        now_utc=detected_time,
    )
    if not dedupe_check.get("send"):
        return {"status": "alert_skipped_duplicate", **dedupe_check}

    message = _build_premium_alert_message(
        title="LIQUIDITY TARGET UPDATE",
        symbol=symbol_value,
        detected_at=detected_time,
        permission_source=permission_source_value,
        permission_stage=permission_stage_value,
        daily_permission=daily_permission,
        final_allowed=final_allowed_value,
        h1_confirmation=h1_confirmation_value,
        m15_opportunity=m15_opportunity_value,
        confidence=confidence,
        magnet=magnet,
        zone_target=zone_target,
        sellside=sellside,
        buyside=buyside,
        risk_state=_derive_risk_state(
            final_allowed=final_allowed_value,
            h1_confirmation=h1_confirmation_value,
            permission_source=permission_source_value,
            permission_stage=permission_stage_value,
            risk_state=risk_state,
        ),
        freshness=freshness or _derive_freshness(detected_at=detected_time),
        reason=reason_value or "Liquidity magnet or zone target changed.",
    )
    if not send_telegram_signal(message):
        return {"status": "alert_failed", **dedupe_check}

    record_alert_sent(scope=scope, fingerprint=fingerprint, sent_at=detected_time, payload=fingerprint)
    return {"status": "alert_sent", **dedupe_check}


def maybe_send_m15_opportunity_confirmed_alert(
    *,
    symbol: str,
    detected_at: datetime | None,
    permission_source: str | None,
    permission_stage: str | None,
    daily_permission: str | None,
    final_allowed: str | None,
    h1_confirmation: str | None,
    m15_opportunity: str | None,
    confidence: float | None,
    reason: str | None,
    magnet: float | None,
    zone_target: float | None,
    sellside: float | None,
    buyside: float | None,
    material_refresh: bool,
    risk_state: str | None = None,
    freshness: str | None = None,
    active_setup_key: str | None = None,
) -> dict:
    symbol_value = (symbol or "").strip().upper()
    detected_time = _as_utc(detected_at)
    permission_source_value = (permission_source or "").strip().upper()
    permission_stage_value = (permission_stage or "").strip().upper()
    daily_permission_value = (daily_permission or "").strip().upper()
    final_allowed_value = (final_allowed or "").strip().upper()
    h1_confirmation_value = (h1_confirmation or "").strip().upper()
    m15_opportunity_value = (m15_opportunity or "").strip().upper()
    reason_value = (reason or "").strip()

    if not (
        final_allowed_value in {"BUY_ONLY", "SELL_ONLY"}
        and h1_confirmation_value == "CONFIRMED"
        and m15_opportunity_value in {"BUY_ONLY", "SELL_ONLY"}
    ):
        return {"status": "not_applicable", "reason": "opportunity_incomplete"}

    fingerprint = {
        "symbol": symbol_value,
        "signal_type": "opportunity_m15_confirmed",
        "permission_source": permission_source_value,
        "permission_stage": permission_stage_value,
        "daily_permission": daily_permission_value,
        "final_allowed": final_allowed_value,
        "h1_confirmation": h1_confirmation_value,
        "m15_opportunity": m15_opportunity_value,
        "date_uk": detected_time.astimezone(UK_TZ).date().isoformat(),
        "active_setup_key": _normalize_text(active_setup_key) or "",
    }
    scope = f"{symbol_value}::m15_opportunity_confirmed"
    dedupe_check = should_send_alert(
        scope=scope,
        fingerprint=fingerprint,
        material_refresh=False,
        now_utc=detected_time,
    )
    if not dedupe_check.get("send"):
        return {"status": "alert_skipped_duplicate", **dedupe_check}

    message = _build_premium_alert_message(
        title="M15 OPPORTUNITY CONFIRMED",
        symbol=symbol_value,
        detected_at=detected_time,
        permission_source=permission_source_value,
        permission_stage=permission_stage_value,
        daily_permission=daily_permission_value,
        final_allowed=final_allowed_value,
        h1_confirmation=h1_confirmation_value,
        m15_opportunity=m15_opportunity_value,
        confidence=confidence,
        magnet=magnet,
        zone_target=zone_target,
        sellside=sellside,
        buyside=buyside,
        risk_state=_derive_risk_state(
            final_allowed=final_allowed_value,
            h1_confirmation=h1_confirmation_value,
            permission_source=permission_source_value,
            permission_stage=permission_stage_value,
            risk_state=risk_state,
        ),
        freshness=freshness or _derive_freshness(detected_at=detected_time),
        reason=reason_value or "M15 opportunity aligned with daily permission and H1 confirmation.",
    )
    if not send_telegram_signal(message):
        return {"status": "alert_failed", **dedupe_check}

    record_alert_sent(scope=scope, fingerprint=fingerprint, sent_at=detected_time, payload=fingerprint)
    return {"status": "alert_sent", **dedupe_check}


def build_risk_stale_warning_message(
    *,
    symbol: str,
    detected_at: datetime | None,
    permission_source: str | None,
    permission_stage: str | None,
    daily_permission: str | None,
    final_allowed: str | None,
    h1_confirmation: str | None,
    m15_opportunity: str | None,
    confidence: float | None,
    reason: str | None,
    magnet: float | None,
    zone_target: float | None,
    sellside: float | None,
    buyside: float | None,
    risk_state: str | None = None,
    freshness: str | None = None,
) -> str:
    return _build_premium_alert_message(
        title="RISK / STALE WARNING",
        symbol=symbol,
        detected_at=detected_at,
        permission_source=permission_source,
        permission_stage=permission_stage,
        daily_permission=daily_permission,
        final_allowed=final_allowed,
        h1_confirmation=h1_confirmation,
        m15_opportunity=m15_opportunity,
        confidence=confidence,
        magnet=magnet,
        zone_target=zone_target,
        sellside=sellside,
        buyside=buyside,
        risk_state=_derive_risk_state(
            final_allowed=final_allowed,
            h1_confirmation=h1_confirmation,
            permission_source=permission_source,
            permission_stage=permission_stage,
            risk_state=risk_state or "DEGRADED",
        ),
        freshness=freshness or _derive_freshness(detected_at=detected_at, stale_hint=True),
        reason=reason,
    )


def send_telegram_signal(message: str) -> bool:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (settings.TELEGRAM_CHAT_ID or "").strip()
    if not token or not chat_id:
        logger.warning("alert_failed reason=telegram_env_missing")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (message or "").replace("\x00", "").replace("\r", "")[:4000],
                "disable_web_page_preview": True,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not bool(body.get("ok")):
            logger.error("alert_failed reason=telegram_api_non_ok body=%s", body)
            return False
        return True
    except Exception:
        logger.exception("alert_failed reason=telegram_send_exception")
        return False
