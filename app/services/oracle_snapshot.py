from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.oracle_engine import (
    compute_daily_permission_from_m1,
    compute_opportunity_with_h1_confirmation,
    compute_weekly_range_snapshot,
)

logger = logging.getLogger(__name__)


def regime_from_direction(direction: str) -> str:
    if direction == "BUY_ONLY":
        return "bullish"
    if direction == "SELL_ONLY":
        return "bearish"
    return "range"


def compute_dual_timeframe_snapshot(db: Session, symbol: str) -> dict[str, Any]:
    permission = compute_daily_permission_from_m1(db, symbol=symbol)
    opp = compute_opportunity_with_h1_confirmation(db, symbol=symbol, daily_permission=permission.daily_permission)
    opp_public = opp.public_json if isinstance(opp.public_json, dict) else {}
    weekly_range: dict[str, Any] = {}
    try:
        weekly = compute_weekly_range_snapshot(db, symbol=symbol, as_of_utc=opp.as_of_utc)
        weekly_range = {
            "symbol": weekly.symbol,
            "week_key": weekly.week_key,
            "week_start_uk": weekly.week_start_uk.isoformat(),
            "high": weekly.high,
            "low": weekly.low,
            "mid": weekly.mid,
            "range_ready": bool(weekly.range_ready),
            "status": "Locked" if weekly.range_ready else "Building",
            "as_of_utc": weekly.as_of_utc.isoformat(),
            "meta_json": weekly.meta_json,
        }
    except Exception:
        logger.exception("weekly range compute failed during live snapshot symbol=%s", symbol)

    if permission.daily_permission == "BUY_ONLY":
        daily_bias = "bullish"
    elif permission.daily_permission == "SELL_ONLY":
        daily_bias = "bearish"
    else:
        daily_bias = "neutral"

    return {
        "symbol": symbol,
        "as_of": opp.as_of_utc,
        "timeframes": {"signal": "M15", "confirm": "H1", "daily": "M1"},
        "fast_bias": opp.opportunity_direction,
        "opportunity_direction": opp.opportunity_direction,
        "confirm_tf": "H1",
        "confirm_ok": opp.h1_confirm_ok,
        "bias_m1": permission.daily_permission,
        "daily_permission": permission.daily_permission,
        "daily_permission_as_of_utc": permission.as_of_utc.isoformat(),
        "confirm_h1": opp.h1_confirm_ok,
        "final_allowed": opp.final_allowed,
        "final_allowed_basic": opp.final_allowed,
        "final_allowed_elite": opp.final_allowed,
        "daily_bias": daily_bias,
        "daily_alignment": opp.aligned,
        "volume_ok": True,
        "volume_state": "normal",
        "risk_gate_pass": bool(opp_public.get("risk_gate_pass", True)),
        "news_gate_pass": bool(opp_public.get("news_gate_pass", True)),
        "news_blocked_window": None,
        "atr_h1": opp_public.get("atr_h1"),
        "adr_d1": opp_public.get("adr_d1"),
        "reason_basic": opp.reason,
        "next_liquidity_magnet": None,
        "zone_to_zone_target": None,
        "targets_json": {},
        "public_tier": {
            "basic": {"final_allowed": opp.final_allowed, "confidence": opp.confidence},
            "pro": {},
            "elite": {},
        },
        "risk_banner": {},
        "weekly_range": weekly_range,
        "confidence": opp.confidence,
        "candle": {
            "open": opp_public.get("m15_open"),
            "high": None,
            "low": None,
            "close": opp_public.get("m15_close"),
            "volume": None,
        },
        "internal": {
            "daily_permission": permission.factors_json,
            "opportunity": opp.internal_json,
        },
    }
