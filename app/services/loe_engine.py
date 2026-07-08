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


def _candle_range(candle: Any) -> float:
    high = _num(getattr(candle, "high", None)) or 0.0
    low = _num(getattr(candle, "low", None)) or 0.0
    return max(high - low, 0.0)


def evaluate_loe(candles: list[Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if len(candles) < 5:
        return fallback_loe("Need at least 5 candles for LOE.")

    recent = candles[-min(len(candles), 12) :]
    avg_range = mean([_candle_range(candle) for candle in recent]) or 0.0001
    volumes = [_num(getattr(candle, "volume", None)) for candle in recent[:-1]]
    volumes = [value for value in volumes if value is not None and value > 0]
    avg_volume = mean(volumes) if volumes else None

    buy_score = 0.0
    sell_score = 0.0
    for idx, candle in enumerate(recent):
        open_value = _num(getattr(candle, "open", None))
        high = _num(getattr(candle, "high", None))
        low = _num(getattr(candle, "low", None))
        close = _num(getattr(candle, "close", None))
        if open_value is None or high is None or low is None or close is None:
            continue
        rng = max(high - low, 0.0001)
        body = abs(close - open_value)
        body_ratio = body / rng
        upper_wick = high - max(open_value, close)
        lower_wick = min(open_value, close) - low
        weight = 1.0 + idx / max(len(recent), 1)

        if close > open_value:
            buy_score += (0.45 + body_ratio) * weight
        elif close < open_value:
            sell_score += (0.45 + body_ratio) * weight

        if body_ratio >= 0.62 and rng >= avg_range * 1.15:
            if close > open_value:
                buy_score += 0.7 * weight
            elif close < open_value:
                sell_score += 0.7 * weight

        if lower_wick >= rng * 0.45:
            buy_score += 0.35 * weight
        if upper_wick >= rng * 0.45:
            sell_score += 0.35 * weight

        volume = _num(getattr(candle, "volume", None))
        if avg_volume and volume and volume >= avg_volume * 1.25:
            if close >= open_value:
                buy_score += 0.4 * weight
            else:
                sell_score += 0.4 * weight

    total = max(buy_score + sell_score, 0.0001)
    buy_pct = round((buy_score / total) * 100.0, 1)
    sell_pct = round(100.0 - buy_pct, 1)
    spread = abs(buy_pct - sell_pct)
    confidence = round(min(95.0, 45.0 + spread * 0.55), 1)

    if buy_pct >= sell_pct + 8:
        bias = "BUYERS_BUILDING"
        reasons.append("Recent candles show bullish displacement/body dominance.")
    elif sell_pct >= buy_pct + 8:
        bias = "SELLERS_BUILDING"
        reasons.append("Recent candles show bearish displacement/body dominance.")
    else:
        bias = "NEUTRAL"
        reasons.append("Buy and sell pressure are balanced.")
    if avg_volume:
        reasons.append("Volume expansion was included where available.")
    else:
        reasons.append("Volume data unavailable; LOE used candle structure only.")

    return {
        "orderflow_bias": bias,
        "buy_pressure_percent": buy_pct,
        "sell_pressure_percent": sell_pct,
        "confidence": confidence,
        "reason": reasons,
    }


def fallback_loe(error: str | None = None) -> dict[str, Any]:
    reasons = ["LOE unavailable; using neutral orderflow."]
    if error:
        reasons.append(error)
    return {
        "orderflow_bias": "NEUTRAL",
        "buy_pressure_percent": 50.0,
        "sell_pressure_percent": 50.0,
        "confidence": 0.0,
        "reason": reasons,
    }
