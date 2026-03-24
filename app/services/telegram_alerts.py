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

    lines = [
        f"DAILY ALIGNMENT CONFIRMED - {symbol_value}",
        f"London Time: {_format_london(detected_time)}",
        f"Permission Source: {permission_source_value} ({permission_stage_value})",
        f"Daily Permission: {daily_permission_value}",
        f"Final Allowed: {final_allowed_value}",
        f"H1 Confirmation: {h1_confirmation_value or '-'}",
        f"M15 Opportunity: {m15_opportunity_value or '-'}",
        f"Confidence: {_format_confidence(confidence)}",
        f"Reason: {reason_value or '-'}",
        f"Magnet: {_format_price(magnet)}",
        f"Zone Target: {_format_price(zone_target)}",
        f"Sellside: {_format_price(sellside)}",
        f"Buyside: {_format_price(buyside)}",
    ]
    if not send_telegram_signal("\n".join(lines)):
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
    permission_source: str | None = None,
    permission_stage: str | None = None,
    final_allowed: str | None = None,
    h1_confirmation: str | None = None,
    m15_opportunity: str | None = None,
    confidence: float | None = None,
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

    lines = [
        f"LIQUIDITY TARGET UPDATE - {symbol_value}",
        f"London Time: {_format_london(detected_time)}",
    ]
    if permission_source_value:
        source_text = permission_source_value
        if permission_stage_value:
            source_text = f"{source_text} ({permission_stage_value})"
        lines.append(f"Permission Source: {source_text}")
    if final_allowed_value:
        lines.append(f"Final Allowed: {final_allowed_value}")
    if h1_confirmation_value:
        lines.append(f"H1 Confirmation: {h1_confirmation_value}")
    if m15_opportunity_value:
        lines.append(f"M15 Opportunity: {m15_opportunity_value}")
    if confidence is not None:
        lines.append(f"Confidence: {_format_confidence(confidence)}")
    lines.extend(
        [
            f"Reason: {reason_value or '-'}",
            f"Magnet: {_format_price(magnet)}",
            f"Zone Target: {_format_price(zone_target)}",
            f"Sellside: {_format_price(sellside)}",
            f"Buyside: {_format_price(buyside)}",
        ]
    )
    if not send_telegram_signal("\n".join(lines)):
        return {"status": "alert_failed", **dedupe_check}

    record_alert_sent(scope=scope, fingerprint=fingerprint, sent_at=detected_time, payload=fingerprint)
    return {"status": "alert_sent", **dedupe_check}


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
