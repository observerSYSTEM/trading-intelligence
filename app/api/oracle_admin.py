from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.admin_oracle_automation import run_oracle_and_send
from app.services.oracle_scheduler import broadcast_admin_message, run_confirm_now, run_oracle_now
from app.services.strategy_matrix import DAILY_BIAS

router = APIRouter(prefix="/admin", tags=["admin-oracle"])


class RunNowIn(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)


class BroadcastIn(BaseModel):
    tier_min: Literal["basic", "pro", "elite"] = "basic"
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    strategy_name: Literal["DAILY_BIAS", "LIQ_SWEEP", "NEWS_EXEC", "VOL_MANIP", "ZONE_TO_ZONE"] = DAILY_BIAS
    title: str = Field(..., min_length=1, max_length=160)
    message: str = Field(..., min_length=1, max_length=2000)


class RunAndSendIn(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD"], min_length=1)
    tier_min: Literal["basic", "pro", "elite"] = "basic"
    mode: Literal["daily_bias", "intraday_update"] = "daily_bias"
    dry_run: bool = False


def _require_admin(user: User = Depends(get_current_user)) -> User:
    if getattr(user, "role", "user") != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user


@router.post("/oracle/run-now")
def admin_oracle_run_now(
    payload: RunNowIn,
    _admin: User = Depends(_require_admin),
):
    result = run_oracle_now(symbol=payload.symbol.strip().upper())
    return {"ok": True, **result}


@router.post("/oracle/run")
def admin_oracle_run_alias(
    payload: RunNowIn,
    _admin: User = Depends(_require_admin),
):
    result = run_oracle_now(symbol=payload.symbol.strip().upper())
    return {"ok": True, **result}


@router.post("/oracle/run-and-send")
def admin_oracle_run_and_send_alias(
    payload: RunAndSendIn,
    _admin: User = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    return run_oracle_and_send(
        db,
        symbols=payload.symbols,
        tier_min=payload.tier_min,
        mode=payload.mode,
        dry_run=payload.dry_run,
        admin_user_id=_admin.id,
    )


@router.post("/oracle/confirm/{run_id}")
def admin_oracle_confirm_now(
    run_id: str,
    _admin: User = Depends(_require_admin),
):
    return run_confirm_now(run_id)


@router.post("/signals/send")
def admin_signals_send(
    payload: BroadcastIn,
    _admin: User = Depends(_require_admin),
):
    return broadcast_admin_message(
        symbol=payload.symbol.strip().upper(),
        tier_min=payload.tier_min,
        title=payload.title.strip(),
        message=payload.message.strip(),
        strategy_name=payload.strategy_name,
    )
