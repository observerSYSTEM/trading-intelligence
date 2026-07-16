from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.oracle import (
    _build_oracle_direction_payload,
    _latest_snapshot,
    _resolve_plan,
    _resolve_requested_symbol,
    _selected_symbols_for_user,
)
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import allowed_symbols_for_plan
from app.db.models import User
from app.db.session import get_db
from app.services.liquidity_checkpoint_engine import get_liquidity_checkpoint
from app.services.observer_decision_engine import build_observer_decision, extract_daily_bias_from_snapshot
from app.services.observer_recommendation_engine import build_observer_recommendation

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/observer", tags=["observer"])


def _resolve_observer_symbol(db: Session, *, user: User, symbol: str | None) -> tuple[str, str]:
    plan = _resolve_plan(db, user)
    allowed = allowed_symbols_for_plan(plan)
    selected = _selected_symbols_for_user(db, user, plan)
    symbol_value = _resolve_requested_symbol(symbol, allowed=allowed, selected=selected)
    return symbol_value, plan


def _latest_daily_bias(db: Session, *, symbol: str, plan: str) -> str | None:
    return extract_daily_bias_from_snapshot(_latest_snapshot(db, symbol), plan=plan)


def _observer_decision_payload(
    db: Session,
    *,
    symbol: str,
    plan: str,
    timeframe: str,
    lookback: int,
) -> dict[str, Any]:
    lce_result = get_liquidity_checkpoint(db, symbol=symbol, timeframe=timeframe, lookback=lookback)
    oracle_direction = _build_oracle_direction_payload(db, symbol=symbol, plan=plan)
    daily_bias = _latest_daily_bias(db, symbol=symbol, plan=plan)
    return build_observer_decision(
        symbol=symbol,
        timeframe=timeframe.strip().upper(),
        lce_result=lce_result,
        oracle_direction=oracle_direction,
        daily_bias=daily_bias,
    )


@router.get("/decision/{symbol}")
def get_observer_decision(
    symbol: str,
    timeframe: str = Query(default="H1"),
    lookback: int = Query(default=100, ge=12, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("observer_decision", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value, plan = _resolve_observer_symbol(db, user=user, symbol=symbol)
    try:
        return _observer_decision_payload(
            db,
            symbol=symbol_value,
            plan=plan,
            timeframe=timeframe,
            lookback=lookback,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("observer_decision_failed symbol=%s timeframe=%s", symbol_value, timeframe)
        return {
            "symbol": symbol_value,
            "timeframe": timeframe.strip().upper(),
            "decision": "NEUTRAL",
            "confidence": 0.0,
            "agreement_score": 0.0,
            "engine_votes": {
                "oracle": "NEUTRAL",
                "lce": "NEUTRAL",
                "tlee": "PASS",
                "loe": "NEUTRAL",
                "rre": "PASS",
                "ppe": "NEUTRAL",
                "daily_bias": "NEUTRAL",
            },
            "reasoning": [f"Observer Decision Engine failed safely: {exc}"],
            "risk_grade": "HIGH",
            "institutional_alignment": False,
            "source_context": {},
        }


@router.get("/recommendation/{symbol}")
def get_observer_recommendation(
    symbol: str,
    timeframe: str = Query(default="H1"),
    lookback: int = Query(default=100, ge=12, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("observer_recommendation", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value, plan = _resolve_observer_symbol(db, user=user, symbol=symbol)
    try:
        decision = _observer_decision_payload(
            db,
            symbol=symbol_value,
            plan=plan,
            timeframe=timeframe,
            lookback=lookback,
        )
        return build_observer_recommendation(ode_result=decision)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("observer_recommendation_failed symbol=%s timeframe=%s", symbol_value, timeframe)
        return {
            "symbol": symbol_value,
            "timeframe": timeframe.strip().upper(),
            "recommended_action": "DO_NOT_TRADE",
            "execution_quality": "C",
            "confidence_text": "Low",
            "suggested_entry": None,
            "suggested_sl": None,
            "suggested_tp1": None,
            "suggested_tp2": None,
            "expected_liquidity_path": "WAIT",
            "institutional_notes": [f"Observer Recommendation Engine failed safely: {exc}"],
            "risk_notes": ["Risk grade is HIGH.", "No trade action should be taken from a failed observer response."],
            "ode": {},
        }
