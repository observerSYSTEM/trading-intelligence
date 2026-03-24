from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, get_current_user
from app.db.models import Subscription, User

PLAN_ORDER = {"basic": 1, "pro": 2, "elite": 3}
ACTIVE = {"active", "trialing"}

def require_tier(min_tier: str):
    def _guard(
        db: Session = Depends(get_db),
        user: User = Depends(get_current_user),
    ) -> User:
        if getattr(user, "role", None) == "admin":
            return user

        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        if not sub or (sub.status or "").lower() not in ACTIVE:
            raise HTTPException(status_code=402, detail="Subscription required")

        tier = (sub.plan or "basic").lower()
        if PLAN_ORDER.get(tier, 0) < PLAN_ORDER.get(min_tier, 99):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{min_tier.upper()} plan required",
            )
        return user
    return _guard
