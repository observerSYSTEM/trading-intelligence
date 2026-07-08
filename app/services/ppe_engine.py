from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _round_price(value: float) -> float:
    if abs(value) >= 100:
        return round(value, 2)
    if abs(value) >= 10:
        return round(value, 3)
    if abs(value) >= 1:
        return round(value, 5)
    return round(value, 6)


def evaluate_ppe(candles: list[Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if len(candles) < 10:
        return fallback_ppe("Need at least 10 candles for PPE.")

    window = candles[-min(len(candles), 80) :]
    highs = [_num(getattr(candle, "high", None)) for candle in window]
    lows = [_num(getattr(candle, "low", None)) for candle in window]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    current = _num(getattr(candles[-1], "close", None))
    if not highs or not lows or current is None:
        return fallback_ppe("Range or current price unavailable.")

    range_high = max(highs)
    range_low = min(lows)
    equilibrium = (range_high + range_low) / 2.0
    span = max(range_high - range_low, 0.0001)
    distance_from_eq = abs(current - equilibrium) / span

    if distance_from_eq <= 0.06:
        zone = "EQUILIBRIUM"
        preferred = "WAIT"
        reasons.append("Current price is near the 50% equilibrium of the recent range.")
    elif current > equilibrium:
        zone = "PREMIUM"
        preferred = "SELLSIDE_SWEEP_OR_SHORTS"
        reasons.append("Current price is in premium relative to the recent swing range.")
    else:
        zone = "DISCOUNT"
        preferred = "BUYSIDE_SWEEP_OR_LONGS"
        reasons.append("Current price is in discount relative to the recent swing range.")

    return {
        "range_high": _round_price(range_high),
        "range_low": _round_price(range_low),
        "equilibrium": _round_price(equilibrium),
        "price_zone": zone,
        "preferred_action": preferred,
        "reason": reasons,
    }


def fallback_ppe(error: str | None = None) -> dict[str, Any]:
    reasons = ["PPE unavailable; using neutral premium/discount context."]
    if error:
        reasons.append(error)
    return {
        "range_high": None,
        "range_low": None,
        "equilibrium": None,
        "price_zone": "UNKNOWN",
        "preferred_action": "WAIT",
        "reason": reasons,
    }
