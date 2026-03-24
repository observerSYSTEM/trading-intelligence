from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.db.session import get_db
from app.services.usage_service import usage_snapshot_for_user

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def usage(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("usage_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    _, payload = usage_snapshot_for_user(db, user.id)
    db.commit()
    return payload


@router.get("/signals")
def usage_signals_alias(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("usage_signals_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    _, payload = usage_snapshot_for_user(db, user.id)
    db.commit()
    return payload
