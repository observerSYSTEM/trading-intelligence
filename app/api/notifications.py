from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import NotificationRoute, User
from app.db.session import get_db
from app.services.telegram import send_telegram_message

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _mask_chat_id(chat_id: str | None) -> str:
    raw = (chat_id or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


class NotificationTelegramIn(BaseModel):
    telegram_enabled: bool = True
    chat_id: str | None = Field(default=None, min_length=5, max_length=32, pattern=r"^-?\d+$")


@router.get("")
def get_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("notifications_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    if not route:
        return {"telegram_enabled": False, "telegram_chat_id": ""}

    return {
        "telegram_enabled": bool(route.telegram_enabled),
        "telegram_chat_id": _mask_chat_id(route.telegram_chat_id),
    }


@router.post("/telegram")
def set_telegram(
    payload: NotificationTelegramIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("notifications_set", (RateLimitRule(limit=30, window_seconds=60),)),
):
    telegram_enabled = bool(payload.telegram_enabled)
    chat_id = (payload.chat_id or "").strip()

    if telegram_enabled and not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required when enabling Telegram")

    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    if not route:
        route = NotificationRoute(user_id=user.id)
        db.add(route)

    route.telegram_enabled = telegram_enabled
    route.telegram_chat_id = chat_id if telegram_enabled else None
    db.commit()

    return {"ok": True}


@router.post("/telegram/test")
def test_telegram(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("notifications_test", (RateLimitRule(limit=10, window_seconds=60),)),
):
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    if not route or not route.telegram_enabled or not route.telegram_chat_id:
        raise HTTPException(status_code=400, detail="Telegram is not connected")

    send_telegram_message(route.telegram_chat_id, "Test message received. Telegram is connected.")
    return {"ok": True}
