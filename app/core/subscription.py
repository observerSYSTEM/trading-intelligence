from datetime import datetime
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_current_user
from app.db.models import Subscription, User

ACTIVE_STATUSES = {"active", "trialing"}  # trialing counts as paid

TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


def require_active_subscription(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> User:
    # Optional admin bypass
    if getattr(user, "role", None) == "admin":
        return user

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Subscription required",
        )

    if (sub.status or "").lower() not in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Subscription not active (status={sub.status})",
        )

    if sub.current_period_end and sub.current_period_end < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Subscription expired",
        )

    return user


def require_tier(min_tier: str):
    if min_tier not in TIER_ORDER:
        raise ValueError(f"Unknown tier: {min_tier}")

    min_rank = TIER_ORDER[min_tier]

    def _guard(
        db: Session = Depends(get_db),
        user: User = Depends(require_active_subscription),  # ✅ enforce active first
    ) -> User:
        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        plan = (sub.plan or "basic").lower() if sub else "basic"

        if TIER_ORDER.get(plan, 0) < min_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{min_tier.upper()} plan required",
            )
        return user

    return _guard
