from __future__ import annotations

from datetime import date

import requests
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import TelegramThreadState


def _api_url(method: str) -> str:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    return f"https://api.telegram.org/bot{token}/{method}"


def _telegram_request(method: str, payload: dict) -> dict:
    response = requests.post(_api_url(method), json=payload, timeout=20)
    if not response.ok:
        raise RuntimeError(f"Telegram API error: {response.status_code} {response.text}")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API returned failure: {data}")
    return data


def _sanitize_text(text: str) -> str:
    return text.replace("\x00", "").replace("\r", "")[:4000]


def send_message(
    chat_id: str,
    text: str,
    reply_to_message_id: int | None = None,
    disable_preview: bool = True,
) -> int:
    payload: dict = {
        "chat_id": chat_id,
        "text": _sanitize_text(text),
        "disable_web_page_preview": disable_preview,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    data = _telegram_request("sendMessage", payload)
    result = data.get("result") or {}
    message_id = result.get("message_id")
    if not isinstance(message_id, int):
        raise RuntimeError(f"Telegram API missing message_id: {data}")
    return message_id


def pin_message(chat_id: str, message_id: int) -> None:
    _telegram_request(
        "pinChatMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": True,
        },
    )


def unpin_message(chat_id: str, message_id: int) -> None:
    _telegram_request(
        "unpinChatMessage",
        {
            "chat_id": chat_id,
            "message_id": int(message_id),
        },
    )


def ensure_pinned_bias(
    db: Session,
    *,
    chat_id: str,
    symbol: str,
    date_uk: date,
    anchor_text: str | None = None,
    pin_bool: bool,
    rotate_if_exists: bool = False,
) -> int:
    safe_chat_id = str(chat_id).strip()
    safe_symbol = str(symbol).strip().upper()
    bias_text = anchor_text or f"DAILY BIAS - {safe_symbol}\nDate: {date_uk.isoformat()}\nThread: Daily intelligence updates."
    existing = (
        db.query(TelegramThreadState)
        .filter(
            TelegramThreadState.chat_id == safe_chat_id,
            TelegramThreadState.symbol == safe_symbol,
            TelegramThreadState.date_uk == date_uk,
        )
        .first()
    )
    if existing:
        if not rotate_if_exists:
            return int(existing.pinned_message_id)

        old_anchor_id = int(existing.pinned_message_id)
        new_anchor_id = send_message(safe_chat_id, bias_text)
        existing.pinned_message_id = new_anchor_id
        db.add(existing)
        db.flush()
        if pin_bool:
            try:
                try:
                    unpin_message(safe_chat_id, old_anchor_id)
                except Exception:
                    pass
                pin_message(safe_chat_id, new_anchor_id)
            except Exception:
                pass
        return int(new_anchor_id)

    previous = (
        db.query(TelegramThreadState)
        .filter(
            TelegramThreadState.chat_id == safe_chat_id,
            TelegramThreadState.symbol == safe_symbol,
            TelegramThreadState.date_uk < date_uk,
        )
        .order_by(TelegramThreadState.date_uk.desc(), TelegramThreadState.created_at.desc())
        .first()
    )

    anchor_message_id = send_message(safe_chat_id, bias_text)
    row = TelegramThreadState(
        chat_id=safe_chat_id,
        symbol=safe_symbol,
        date_uk=date_uk,
        pinned_message_id=anchor_message_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        race = (
            db.query(TelegramThreadState)
            .filter(
                TelegramThreadState.chat_id == safe_chat_id,
                TelegramThreadState.symbol == safe_symbol,
                TelegramThreadState.date_uk == date_uk,
            )
            .first()
        )
        if race:
            return int(race.pinned_message_id)
        raise

    if pin_bool:
        try:
            if previous:
                try:
                    unpin_message(safe_chat_id, int(previous.pinned_message_id))
                except Exception:
                    pass
            pin_message(safe_chat_id, anchor_message_id)
        except Exception:
            # Some chats/channels may disallow pinning; we keep thread state regardless.
            pass

    return anchor_message_id


def ensure_daily_anchor(
    db: Session,
    *,
    user_id,
    chat_id: str,
    symbol: str,
    date_uk: date,
    anchor_text: str,
    pin_bool: bool,
    rotate_if_exists: bool = False,
) -> int:
    del user_id
    return ensure_pinned_bias(
        db,
        chat_id=chat_id,
        symbol=symbol,
        date_uk=date_uk,
        anchor_text=anchor_text,
        pin_bool=pin_bool,
        rotate_if_exists=rotate_if_exists,
    )


def send_reply(chat_id: str, pinned_message_id: int, text: str) -> int:
    return send_message(chat_id, text, reply_to_message_id=pinned_message_id)


def send_thread_update(
    db: Session,
    *,
    user_id,
    chat_id: str,
    symbol: str,
    date_uk: date,
    title: str,
    body: str,
    time_london: str,
    pin_bool: bool = True,
    anchor_text: str | None = None,
    rotate_anchor: bool = False,
) -> dict:
    del user_id
    anchor_id = ensure_pinned_bias(
        db,
        chat_id=chat_id,
        symbol=symbol,
        date_uk=date_uk,
        anchor_text=anchor_text,
        pin_bool=pin_bool,
        rotate_if_exists=rotate_anchor,
    )

    text = f"{title}\n{body}\nAs of: {time_london}"
    message_id = send_reply(chat_id=chat_id, pinned_message_id=anchor_id, text=text)

    return {"anchor_message_id": anchor_id, "message_id": message_id}


def send_telegram(chat_id: str, text: str) -> bool:
    send_message(chat_id=chat_id, text=text)
    return True
