from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlsplit

import requests

from app.core.config import settings
from app.schemas.signal import SignalCreate
from app.services.signal_service import build_dedup_key

logger = logging.getLogger(__name__)

_RECENT_DEDUP_KEYS: dict[str, datetime] = {}
_DEDUP_LOCK = Lock()
_DEDUP_RETENTION_SECONDS = 1800


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _prune_recent(now_value: datetime) -> None:
    cutoff = now_value.timestamp() - _DEDUP_RETENTION_SECONDS
    stale = [key for key, seen_at in _RECENT_DEDUP_KEYS.items() if seen_at.timestamp() < cutoff]
    for key in stale:
        _RECENT_DEDUP_KEYS.pop(key, None)


def _seen_recently(dedup_key: str, *, now_value: datetime) -> bool:
    with _DEDUP_LOCK:
        _prune_recent(now_value)
        return dedup_key in _RECENT_DEDUP_KEYS


def _mark_seen(dedup_key: str, *, now_value: datetime) -> None:
    with _DEDUP_LOCK:
        _prune_recent(now_value)
        _RECENT_DEDUP_KEYS[dedup_key] = now_value


def _resolve_ingest_url() -> str:
    raw = (settings.BACKEND_API_URL or "").strip()
    if raw:
        if raw.endswith("/signals"):
            return raw
        parsed = urlsplit(raw)
        path = parsed.path or ""
        if not path or path == "/":
            return f"{raw.rstrip('/')}{settings.API_VERSION_PREFIX}/signals"
        return f"{raw.rstrip('/')}/signals"
    return f"{settings.APP_URL.rstrip('/')}{settings.API_VERSION_PREFIX}/signals"


def publish_signal(payload: SignalCreate) -> dict:
    token = (settings.SIGNAL_API_TOKEN or "").strip()
    if not token:
        logger.warning("signal publish skipped: SIGNAL_API_TOKEN is not configured")
        return {"ok": False, "skipped": True, "reason": "missing_signal_api_token"}

    now_value = datetime.now(timezone.utc)
    dedup_key = build_dedup_key(payload, bucket_seconds=60)
    if _seen_recently(dedup_key, now_value=now_value):
        return {"ok": True, "skipped": True, "reason": "recent_duplicate", "dedup_key": dedup_key}

    data = payload.model_dump(mode="json")
    data["dedup_key"] = dedup_key
    timeout_seconds = max(int(settings.SIGNAL_POST_TIMEOUT_SECONDS or 10), 1)
    target_url = _resolve_ingest_url()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(target_url, json=data, headers=headers, timeout=timeout_seconds)
    except Exception as exc:
        logger.warning(
            "signal publish failed transport source=%s symbol=%s timeframe=%s error=%s",
            payload.source,
            payload.symbol,
            payload.timeframe,
            exc,
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": "transport_error",
            "error": str(exc),
            "dedup_key": dedup_key,
        }

    if response.status_code >= 400:
        body_preview = (response.text or "")[:300]
        logger.warning(
            "signal publish failed status=%s source=%s symbol=%s timeframe=%s body=%s",
            response.status_code,
            payload.source,
            payload.symbol,
            payload.timeframe,
            body_preview,
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": f"http_{response.status_code}",
            "body": body_preview,
            "dedup_key": dedup_key,
        }

    duplicate = None
    signal_id = None
    signal_payload = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            duplicate = bool(parsed.get("duplicate")) if parsed.get("duplicate") is not None else None
            signal_data = parsed.get("signal")
            if isinstance(signal_data, dict):
                signal_id = signal_data.get("id")
                signal_payload = signal_data
    except Exception:
        pass

    _mark_seen(dedup_key, now_value=now_value)
    logger.info(
        "signal published source=%s symbol=%s timeframe=%s dedup_key=%s duplicate=%s signal_id=%s",
        payload.source,
        payload.symbol,
        payload.timeframe,
        dedup_key[:12],
        duplicate,
        signal_id,
    )
    return {
        "ok": True,
        "status_code": response.status_code,
        "duplicate": duplicate,
        "signal_id": signal_id,
        "signal": signal_payload,
        "dedup_key": dedup_key,
    }
