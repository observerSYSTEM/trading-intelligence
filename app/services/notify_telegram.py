from __future__ import annotations

import logging

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _sanitize(text: str) -> str:
    return (text or "").replace("\x00", "").replace("\r", "")[:4000]


def send_telegram(text: str) -> dict | None:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    chat_id = (settings.TELEGRAM_CHAT_ID or "").strip()
    if not token or not chat_id:
        return None

    payload = {
        "chat_id": chat_id,
        "text": _sanitize(text),
        "disable_web_page_preview": True,
    }
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    try:
        if httpx is not None:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
        else:
            response = requests.post(url, json=payload, timeout=10.0)
    except Exception:
        logger.exception("notify telegram transport failed")
        return None

    data: dict | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            data = parsed
    except Exception:
        data = None

    if response.status_code != 200 or not data or not bool(data.get("ok")):
        description = ""
        if isinstance(data, dict):
            description = str(data.get("description") or "")
        if not description:
            description = response.text[:300]
        logger.error("notify telegram failed status=%s description=%s", response.status_code, description)
        return None

    return data
