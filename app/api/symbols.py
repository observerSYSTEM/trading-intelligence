from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import ALL_SYMBOLS
from app.db.models import Subscription, User
from app.db.session import get_db
from app.services.symbol_preferences import (
    available_and_locked,
    get_user_symbol_preferences_payload,
    upsert_user_symbol_preferences,
)

router = APIRouter(prefix="/symbols", tags=["symbols"])


class SymbolPreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected: list[str] = Field(default_factory=list)


def _resolve_user_tier(db: Session, user: User) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return (sub.plan or "basic").lower() if sub else "basic"


@router.get("/available")
def get_symbols_available(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("symbols_available_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    tier = _resolve_user_tier(db, user)
    available, locked = available_and_locked(tier)
    return {
        "tier": tier,
        "available": available,
        "locked": locked,
    }


@router.get("/preferences")
def get_symbols_preferences(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("symbols_preferences_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    tier = _resolve_user_tier(db, user)
    data = get_user_symbol_preferences_payload(db, user.id, tier)
    # Least-privilege response fields.
    return {
        "selected": data["selected"],
        "all": data["all"],
    }


@router.put("/preferences")
def put_symbols_preferences(
    payload: SymbolPreferencesIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("symbols_preferences_put", (RateLimitRule(limit=20, window_seconds=60),)),
):
    tier = _resolve_user_tier(db, user)
    result = upsert_user_symbol_preferences(
        db,
        user_id=user.id,
        plan=tier,
        selected_symbols=payload.selected,
    )

    if not result.get("ok"):
        if result.get("error") == "invalid_symbols":
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "invalid_symbols",
                    "invalid": result.get("invalid", []),
                    "allowlist": ALL_SYMBOLS,
                },
            )
        if result.get("error") == "locked_symbols":
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "locked_symbols",
                    "locked": result.get("locked_selected", []),
                    "available": result.get("available", []),
                },
            )
        raise HTTPException(status_code=400, detail="Invalid symbol preference payload")

    db.commit()
    return {
        "selected": result["selected"],
        "available": result["available"],
        "locked": result["locked"],
    }
