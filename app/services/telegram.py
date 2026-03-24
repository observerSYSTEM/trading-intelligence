from __future__ import annotations

import logging

try:
    import httpx  # type: ignore
except Exception:  # pragma: no cover - runtime fallback when httpx is not installed
    httpx = None  # type: ignore
import requests

from app.core.config import settings

logger = logging.getLogger(__name__)


def _sanitize_text(text: str) -> str:
    safe = text.replace("\x00", "").replace("\r", "")
    return safe[:4000]


def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    disable_preview: bool = True,
) -> dict:
    token = (settings.TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    chat = str(chat_id or "").strip()
    if not chat:
        raise RuntimeError("telegram chat_id is required")

    payload = {
        "chat_id": chat,
        "text": _sanitize_text(text or ""),
        "disable_web_page_preview": bool(disable_preview),
    }

    try:
        if httpx is not None:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload)
        else:
            logger.warning("httpx not installed; using requests fallback for Telegram send.")
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
                timeout=10.0,
            )
    except Exception as exc:
        logger.exception("telegram send transport error")
        raise RuntimeError(f"Telegram request failed: {exc}") from exc

    body: dict | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = None

    if response.status_code != 200:
        description = ""
        if body and isinstance(body.get("description"), str):
            description = body["description"]
        elif response.text:
            description = response.text[:500]
        logger.error("telegram send failed status=%s description=%s", response.status_code, description)
        raise RuntimeError(f"Telegram send failed ({response.status_code}): {description or 'Unknown Telegram error'}")

    if not body or not bool(body.get("ok")):
        description = ""
        if body and isinstance(body.get("description"), str):
            description = body["description"]
        logger.error("telegram send returned non-ok payload description=%s", description)
        raise RuntimeError(f"Telegram send failed: {description or 'Telegram API returned ok=false'}")

    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    message_id = result.get("message_id")
    return {
        "ok": True,
        "result": result,
        "message_id": int(message_id) if isinstance(message_id, int) else None,
    }
