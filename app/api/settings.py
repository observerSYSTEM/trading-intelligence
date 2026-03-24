from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import NotificationRoute, Subscription, User, UserSignalPref
from app.db.session import get_db
from app.services.symbol_preferences import (
    available_and_locked,
    get_user_symbol_preferences_payload,
    upsert_user_symbol_preferences,
)
from app.services.telegram_service import send_message

router = APIRouter(prefix="/settings", tags=["settings"])


def _mask_chat_id(chat_id: str | None) -> str:
    raw = (chat_id or "").strip()
    if not raw:
        return ""
    if len(raw) <= 4:
        return "*" * len(raw)
    return f"{raw[:2]}{'*' * (len(raw) - 4)}{raw[-2:]}"


class TelegramSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    telegram_chat_id: str | None = Field(default=None, min_length=5, max_length=32, pattern=r"^-?\d+$")
    enabled: bool | None = None
    telegram_enabled: bool | None = None
    pin_daily_bias: bool | None = True
    symbols: list[str] | None = None


class SymbolSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[str] = Field(default_factory=list)


def _resolve_user_plan(db: Session, user: User) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return (sub.plan or "basic").lower() if sub else "basic"


@router.get("/telegram")
def get_telegram_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("settings_telegram_get", (RateLimitRule(limit=60, window_seconds=60),)),
):
    plan = _resolve_user_plan(db, user)
    symbol_payload = get_user_symbol_preferences_payload(db, user.id, plan)
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user.id).first()
    pref_enabled = bool(pref.telegram_enabled) if pref else None
    pref_chat_id = (pref.telegram_chat_id or "").strip() if pref else ""
    route_enabled = bool(route.telegram_enabled) if route else False
    route_chat_id = (route.telegram_chat_id or "").strip() if route else ""
    telegram_enabled = pref_enabled if pref_enabled is not None else route_enabled
    raw_chat_id = pref_chat_id or route_chat_id
    if not route:
        return {
            "telegram_enabled": bool(telegram_enabled),
            "telegram_chat_id": _mask_chat_id(raw_chat_id),
            "has_chat_id": bool(raw_chat_id),
            "pin_daily_bias": True,
            "symbols": symbol_payload["selected"],
            "allowed_symbols": symbol_payload["available"],
            "locked_symbols": symbol_payload["locked"],
        }

    return {
        "telegram_enabled": bool(telegram_enabled),
        "telegram_chat_id": _mask_chat_id(raw_chat_id),
        "has_chat_id": bool(raw_chat_id),
        "pin_daily_bias": bool(route.telegram_pin_daily_bias),
        "symbols": symbol_payload["selected"],
        "allowed_symbols": symbol_payload["available"],
        "locked_symbols": symbol_payload["locked"],
    }


@router.post("/telegram")
def set_telegram_settings(
    payload: TelegramSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("settings_telegram_set", (RateLimitRule(limit=30, window_seconds=60),)),
):
    plan = _resolve_user_plan(db, user)
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    if not route:
        route = NotificationRoute(user_id=user.id, email_enabled=True)
        db.add(route)

    input_chat_id = (payload.telegram_chat_id or "").strip()
    pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user.id).first()
    pref_chat_id = (pref.telegram_chat_id or "").strip() if pref else ""
    route_chat_id = (route.telegram_chat_id or "").strip() if route else ""
    existing_chat_id = pref_chat_id or route_chat_id
    pref_enabled = bool(pref.telegram_enabled) if pref else None
    route_enabled = bool(route.telegram_enabled) if route else False
    existing_enabled = pref_enabled if pref_enabled is not None else route_enabled

    requested_enabled: bool | None
    if payload.enabled is not None:
        requested_enabled = bool(payload.enabled)
    elif payload.telegram_enabled is not None:
        requested_enabled = bool(payload.telegram_enabled)
    else:
        requested_enabled = None

    if requested_enabled is not None:
        enabled = requested_enabled
    elif input_chat_id:
        enabled = True
    else:
        enabled = bool(existing_enabled)

    if requested_enabled is True and not input_chat_id:
        raise HTTPException(status_code=400, detail="telegram_chat_id is required when telegram_enabled=true")

    if enabled and not input_chat_id and not existing_chat_id:
        raise HTTPException(status_code=400, detail="telegram_chat_id is required when enabling Telegram")

    if input_chat_id:
        chat_id_to_store = input_chat_id
    elif enabled:
        chat_id_to_store = existing_chat_id
    else:
        chat_id_to_store = None

    route.telegram_enabled = enabled
    route.telegram_chat_id = chat_id_to_store

    if payload.pin_daily_bias is not None:
        route.telegram_pin_daily_bias = bool(payload.pin_daily_bias)

    symbol_result = None
    if payload.symbols is not None:
        symbol_result = upsert_user_symbol_preferences(
            db,
            user_id=user.id,
            plan=plan,
            selected_symbols=payload.symbols,
        )
        if not symbol_result.get("ok"):
            if symbol_result.get("error") == "locked_symbols":
                raise HTTPException(
                    status_code=403,
                    detail={
                        "error": "locked_symbols",
                        "locked": symbol_result.get("locked_selected", []),
                        "available": symbol_result.get("available", []),
                    },
                )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": symbol_result.get("error"),
                    "invalid": symbol_result.get("invalid", []),
                },
            )

    if symbol_result:
        selected_symbols = symbol_result.get("selected", [])
    else:
        current_payload = get_user_symbol_preferences_payload(db, user.id, plan)
        selected_symbols = current_payload["selected"]

    normalized_symbols: list[str] = []
    for item in selected_symbols:
        symbol = str(item).strip().upper()
        if symbol and symbol not in normalized_symbols:
            normalized_symbols.append(symbol)

    now = datetime.now(timezone.utc)
    pref_row = pref
    if pref_row is None:
        # `upsert_user_symbol_preferences` may already stage a pending row in this session.
        pref_row = next(
            (
                obj
                for obj in db.new
                if isinstance(obj, UserSignalPref) and getattr(obj, "user_id", None) == user.id
            ),
            None,
        )
    if pref_row is None:
        pref_row = db.query(UserSignalPref).filter(UserSignalPref.user_id == user.id).first()
    if pref_row is None:
        pref_row = UserSignalPref(user_id=user.id)
        db.add(pref_row)
    pref_row.symbols_json = normalized_symbols
    pref_row.telegram_enabled = bool(enabled)
    pref_row.telegram_chat_id = chat_id_to_store
    pref_row.updated_at = now

    db.commit()
    db.refresh(route)
    saved_chat_id = (chat_id_to_store or "").strip()
    return {
        "ok": True,
        "telegram_enabled": bool(enabled),
        "telegram_chat_id": saved_chat_id,
        "symbols": normalized_symbols,
    }


@router.post("/telegram/test")
def test_telegram_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("settings_telegram_test", (RateLimitRule(limit=10, window_seconds=60),)),
):
    pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user.id).first()
    route = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).first()
    pref_enabled = bool(pref.telegram_enabled) if pref else False
    pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
    route_enabled = bool(route.telegram_enabled) if route else False
    route_chat = (route.telegram_chat_id or "").strip() if route else ""
    enabled = pref_enabled or route_enabled
    chat_id = pref_chat or route_chat
    if not enabled or not chat_id:
        raise HTTPException(status_code=400, detail="Telegram is not connected")

    send_message(chat_id, "Test successful")
    return {"ok": True}


@router.get("/symbols")
def get_symbol_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("settings_symbols_get", (RateLimitRule(limit=60, window_seconds=60),)),
):
    plan = _resolve_user_plan(db, user)
    payload = get_user_symbol_preferences_payload(db, user.id, plan)
    return {
        "tier": payload["tier"],
        "allowed_symbols": payload["available"],
        "selected_symbols": payload["selected"],
    }


@router.post("/symbols")
def set_symbol_settings(
    payload: SymbolSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("settings_symbols_set", (RateLimitRule(limit=30, window_seconds=60),)),
):
    plan = _resolve_user_plan(db, user)
    result = upsert_user_symbol_preferences(
        db,
        user_id=user.id,
        plan=plan,
        selected_symbols=payload.symbols,
    )
    if not result.get("ok"):
        if result.get("error") == "locked_symbols":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "locked_symbols",
                    "locked": result.get("locked_selected", []),
                    "available": result.get("available", []),
                },
            )
        raise HTTPException(
            status_code=400,
            detail={
                "error": result.get("error"),
                "invalid": result.get("invalid", []),
            },
        )

    db.commit()
    available, _locked = available_and_locked(plan)
    return {
        "ok": True,
        "tier": plan,
        "allowed_symbols": available,
        "selected_symbols": result["selected"],
    }
