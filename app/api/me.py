from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.symbols import allowed_symbols_for_plan
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.session import get_db
from app.db.models import Subscription, User
from app.services.symbol_preferences import get_user_enabled_symbols

router = APIRouter(tags=["me"])

@router.get("/me")
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("me_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()

    tier = (sub.plan if sub else "basic") or "basic"
    status = (sub.status if sub else "inactive") or "inactive"

    # Ensure a subscription row always exists
    if not sub:
        sub = Subscription(user_id=user.id, plan="basic", status="inactive")
        db.add(sub)
        db.commit()
        db.refresh(sub)

    symbols_available = allowed_symbols_for_plan(tier)
    symbols_enabled = get_user_enabled_symbols(db, user.id, tier)

    return {
        "id": str(user.id),
        "full_name": (user.full_name or "").strip(),
        "email": user.email,
        "role": user.role,
        "tier": tier,
        "status": status,
        "symbols_enabled": symbols_enabled,
        "symbols_available": symbols_available,
    }



    
    
