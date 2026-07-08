from __future__ import annotations

from statistics import mean
from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _range(candle: Any) -> float:
    high = _num(getattr(candle, "high", None)) or 0.0
    low = _num(getattr(candle, "low", None)) or 0.0
    return max(high - low, 0.0)


def _find_recent_imbalance(candles: list[Any]) -> tuple[float, float] | None:
    for idx in range(len(candles) - 1, 1, -1):
        left = candles[idx - 2]
        right = candles[idx]
        left_high = _num(getattr(left, "high", None))
        left_low = _num(getattr(left, "low", None))
        right_high = _num(getattr(right, "high", None))
        right_low = _num(getattr(right, "low", None))
        if None in {left_high, left_low, right_high, right_low}:
            continue
        if right_low and left_high and right_low > left_high:
            return left_high, right_low
        if right_high and left_low and right_high < left_low:
            return right_high, left_low
    return None


def evaluate_rre(candles: list[Any], *, checkpoint: float | None = None, checkpoint_type: str | None = None) -> dict[str, Any]:
    reasons: list[str] = []
    if len(candles) < 10:
        return fallback_rre("Need at least 10 candles for RRE.")

    current = _num(getattr(candles[-1], "close", None))
    if current is None:
        return fallback_rre("Latest close unavailable.")

    window = candles[-min(len(candles), 30) :]
    high = max((_num(getattr(candle, "high", None)) or current) for candle in window)
    low = min((_num(getattr(candle, "low", None)) or current) for candle in window)
    rng = max(high - low, 0.0001)
    from_high = max(high - current, 0.0) / rng * 100.0
    from_low = max(current - low, 0.0) / rng * 100.0
    depth = min(max(min(from_high, from_low), 0.0), 100.0)

    if depth <= 23.6:
        state = "SHALLOW"
        continuation = 72.0
        reversal = 28.0
        reasons.append("Retracement is shallow relative to the recent range.")
    elif depth <= 61.8:
        state = "MID"
        continuation = 58.0
        reversal = 42.0
        reasons.append("Retracement is inside the mid-range of the recent swing.")
    else:
        state = "DEEP"
        continuation = 38.0
        reversal = 62.0
        reasons.append("Retracement is deep; reversal risk is elevated.")

    imbalance = _find_recent_imbalance(candles)
    if imbalance:
        lo, hi = sorted(imbalance)
        if lo <= current <= hi:
            continuation += 8.0
            reversal -= 6.0
            reasons.append("Current price is inside a recent imbalance/FVG area.")
        else:
            reasons.append("Recent imbalance/FVG detected but current price is outside it.")

    if checkpoint is not None and checkpoint_type:
        avg_range = mean([_range(candle) for candle in window[-10:]]) or 0.0001
        if checkpoint_type == "SELLSIDE_LIQUIDITY" and current > checkpoint and abs(current - checkpoint) <= avg_range * 2:
            continuation += 5.0
            reasons.append("Price is retracing near a sellside sweep zone.")
        if checkpoint_type == "BUYSIDE_LIQUIDITY" and current < checkpoint and abs(current - checkpoint) <= avg_range * 2:
            continuation += 5.0
            reasons.append("Price is retracing near a buyside sweep zone.")

    continuation = round(min(max(continuation, 0.0), 95.0), 1)
    reversal = round(min(max(reversal, 0.0), 95.0), 1)
    return {
        "retracement_state": state,
        "retracement_depth_percent": round(depth, 1),
        "continuation_probability": continuation,
        "reversal_risk": reversal,
        "reason": reasons,
    }


def fallback_rre(error: str | None = None) -> dict[str, Any]:
    reasons = ["RRE unavailable; using neutral retracement context."]
    if error:
        reasons.append(error)
    return {
        "retracement_state": "UNKNOWN",
        "retracement_depth_percent": 0.0,
        "continuation_probability": 50.0,
        "reversal_risk": 50.0,
        "reason": reasons,
    }
