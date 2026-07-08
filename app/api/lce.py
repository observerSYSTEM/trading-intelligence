from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import allowed_symbols_for_plan, normalize_plan
from app.db.models import Subscription, User
from app.db.session import get_db
from app.services.liquidity_checkpoint_engine import get_liquidity_checkpoint
from app.services.loe_engine import fallback_loe
from app.services.ppe_engine import fallback_ppe
from app.services.rre_engine import fallback_rre
from app.services.tlee_engine import fallback_tlee
from app.services.symbol_preferences import get_user_enabled_symbols

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/lce", tags=["liquidity-checkpoint-engine"])


def _resolve_plan(db: Session, user: User) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return normalize_plan(sub.plan if sub else "basic")


def _assert_symbol_access(db: Session, *, user: User, symbol: str) -> None:
    plan = _resolve_plan(db, user)
    allowed = allowed_symbols_for_plan(plan)
    selected = get_user_enabled_symbols(db, user.id, plan)
    if symbol not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol}' is not available on your tier")
    if symbol not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol}' is not enabled in your settings")


@router.get("/checkpoint/{symbol}")
def get_lce_checkpoint(
    symbol: str,
    timeframe: str = Query(default="H1"),
    lookback: int = Query(default=100, ge=12, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("lce_checkpoint", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value = (symbol or "XAUUSD").strip().upper()
    _assert_symbol_access(db, user=user, symbol=symbol_value)
    try:
        return get_liquidity_checkpoint(
            db,
            symbol=symbol_value,
            timeframe=timeframe,
            lookback=lookback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "lce_checkpoint_failed symbol=%s timeframe=%s lookback=%s",
            symbol_value,
            timeframe,
            lookback,
        )
        return {
            "symbol": symbol_value,
            "timeframe": (timeframe or "H1").strip().upper(),
            "status": "ERROR",
            "checkpoint": None,
            "checkpoint_type": None,
            "meaning": "Liquidity Checkpoint Engine could not evaluate this symbol right now.",
            "after_sweep": {"bullish_continuation": [], "bearish_rejection": []},
            "tlee": fallback_tlee(str(exc)),
            "loe": fallback_loe(str(exc)),
            "rre": fallback_rre(str(exc)),
            "ppe": fallback_ppe(str(exc)),
            "final_bias": "WAIT",
            "confidence": 0.0,
            "reason": [str(exc)],
        }
