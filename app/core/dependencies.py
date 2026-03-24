from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.db.models import Subscription, User
from app.db.session import get_db

ACTIVE_STATUSES = {"active", "trialing"}
TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


def require_active_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if getattr(user, "role", None) == "admin":
        return user
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Subscription required")
    if (sub.status or "").lower() not in ACTIVE_STATUSES:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Subscription not active")
    if sub.current_period_end and sub.current_period_end < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail="Subscription expired")
    return user


def require_tier(min_tier: str):
    tier = (min_tier or "").lower().strip()
    if tier not in TIER_ORDER:
        raise ValueError(f"Unknown tier: {min_tier}")
    min_rank = TIER_ORDER[tier]

    def _guard(
        user: User = Depends(require_active_subscription),
        db: Session = Depends(get_db),
    ) -> User:
        if getattr(user, "role", None) == "admin":
            return user
        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        plan = (sub.plan if sub else "basic").lower()
        if TIER_ORDER.get(plan, 0) < min_rank:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{tier.upper()} plan required")
        return user

    return _guard
