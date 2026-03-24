from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.services.usage_service import get_usage


def require_signals_quota(cost: int = 1):
    def _dep(
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ):
        usage = get_usage(db, user.id)
        limit = usage.get("limit")
        remaining = usage.get("remaining")
        if limit is not None and int(remaining or 0) < cost:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "usage_limit_exceeded",
                    "tier": usage.get("tier"),
                    "limit": limit,
                    "used": usage.get("used"),
                    "remaining": remaining,
                    "resets_at": usage.get("resets_at"),
                },
            )
        db.commit()
        return user

    return _dep
