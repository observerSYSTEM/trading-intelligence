from __future__ import annotations

import json
import logging
from typing import Any

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _base_url() -> str | None:
    value = (settings.RUNNER_CONTROL_URL or "").strip()
    if not value:
        return None
    return value.rstrip("/")


def _timeout_seconds() -> int:
    return max(int(settings.RUNNER_CONTROL_TIMEOUT_SECONDS or 10), 2)


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = (settings.RUNNER_API_KEY or "").strip()
    if key:
        headers["X-Runner-Key"] = key
    return headers


def _request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    allow_unconfigured: bool = False,
) -> dict[str, Any]:
    base = _base_url()
    if not base:
        if allow_unconfigured:
            return {
                "configured": False,
                "ok": True,
                "warning": "RUNNER_CONTROL_URL is not configured. Reconnect control is disabled.",
            }
        return {
            "configured": False,
            "ok": False,
            "error": "RUNNER_CONTROL_URL is not configured.",
        }

    url = f"{base}{path}"
    try:
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=_headers(),
            json=payload,
            timeout=_timeout_seconds(),
            verify=bool(settings.RUNNER_CONTROL_VERIFY_TLS),
        )
    except Exception as exc:
        logger.warning("runner control request failed method=%s path=%s error=%s", method, path, exc)
        return {
            "configured": True,
            "ok": False,
            "error": str(exc),
            "status_code": None,
            "url": url,
        }

    body: Any
    try:
        body = response.json()
    except Exception:
        body = response.text

    if response.ok:
        return {
            "configured": True,
            "ok": True,
            "status_code": response.status_code,
            "data": body if isinstance(body, dict) else {"raw": str(body)},
            "url": url,
        }

    message: str
    if isinstance(body, dict):
        try:
            message = json.dumps(body)
        except Exception:
            message = str(body)
    else:
        message = str(body)
    return {
        "configured": True,
        "ok": False,
        "status_code": response.status_code,
        "error": message,
        "url": url,
    }


def fetch_runner_health() -> dict[str, Any]:
    return _request("GET", "/health", allow_unconfigured=True)


def trigger_runner_reconnect(*, reason: str | None = None) -> dict[str, Any]:
    payload = {"reason": (reason or "manual").strip()[:200]}
    return _request("POST", "/reconnect", payload=payload)
