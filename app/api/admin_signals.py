from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.db.session import get_db
from app.services.audit import log_audit
from app.services.oracle_scheduler import broadcast_admin_message
from app.services.strategy_matrix import DAILY_BIAS

router = APIRouter(prefix="/admin/signals", tags=["admin-signals"])


class BroadcastIn(BaseModel):
    tier_min: Literal["basic", "pro", "elite"] = "basic"
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    strategy_name: Literal["DAILY_BIAS", "LIQ_SWEEP", "NEWS_EXEC", "VOL_MANIP", "ZONE_TO_ZONE"] = DAILY_BIAS
    title: str = Field(..., min_length=1, max_length=160)
    message: str = Field(..., min_length=1, max_length=4000)


@router.post("/send")
def admin_signals_send(
    payload: BroadcastIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_signals_send", (RateLimitRule(limit=20, window_seconds=60),)),
):
    log_audit(
        db,
        action="admin.signals.send",
        user_id=_admin.id,
        request=request,
        meta={
            "tier_min": payload.tier_min,
            "symbol": payload.symbol,
            "title": payload.title,
            "strategy_name": payload.strategy_name,
        },
    )
    db.commit()
    return broadcast_admin_message(
        symbol=payload.symbol.strip().upper(),
        tier_min=payload.tier_min,
        title=payload.title.strip(),
        message=payload.message.strip(),
        strategy_name=payload.strategy_name,
    )
