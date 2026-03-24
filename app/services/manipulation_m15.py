from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable


@dataclass
class ManipulationRisk:
    score: int
    level: str
    reasons: list[str]
    volume_z: float
    sweep: bool
    rejection: bool
    range_anomaly: bool
    follow_through_failure: bool


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sample_std(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    return sqrt(max(var, 0.0))


def _atr(values: list, period: int = 14) -> float:
    if len(values) < period + 1:
        return 0.0
    true_ranges: list[float] = []
    for idx in range(1, len(values)):
        c = values[idx]
        p = values[idx - 1]
        high = _as_float(getattr(c, "high", None))
        low = _as_float(getattr(c, "low", None))
        prev_close = _as_float(getattr(p, "close", None))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    window = true_ranges[-period:]
    if not window:
        return 0.0
    return sum(window) / len(window)


def detect_manipulation_m15(candles: Iterable, *, lookback: int = 20, z_window: int = 96) -> ManipulationRisk:
    seq = list(candles)
    if len(seq) < max(lookback + 2, 25):
        return ManipulationRisk(
            score=0,
            level="low",
            reasons=["Insufficient M15 history for manipulation scan."],
            volume_z=0.0,
            sweep=False,
            rejection=False,
            range_anomaly=False,
            follow_through_failure=False,
        )

    # Use the penultimate candle as the impulse and the latest as follow-through.
    impulse = seq[-2]
    follow = seq[-1]
    ref = seq[-(lookback + 2) : -2]

    ref_high = max(_as_float(getattr(c, "high", None)) for c in ref)
    ref_low = min(_as_float(getattr(c, "low", None)) for c in ref)

    i_high = _as_float(getattr(impulse, "high", None))
    i_low = _as_float(getattr(impulse, "low", None))
    i_open = _as_float(getattr(impulse, "open", None))
    i_close = _as_float(getattr(impulse, "close", None))
    i_volume = _as_float(getattr(impulse, "volume", None))

    f_close = _as_float(getattr(follow, "close", None))
    i_range = max(i_high - i_low, 1e-9)
    atr15 = _atr(seq[-40:], period=14)

    volume_ref = [_as_float(getattr(c, "volume", None)) for c in seq[-(z_window + 2) : -2]]
    vol_mean = sum(volume_ref) / len(volume_ref) if volume_ref else 0.0
    vol_std = _sample_std(volume_ref)
    volume_z = (i_volume - vol_mean) / vol_std if vol_std > 0 else 0.0

    sweep_up = i_high > ref_high
    sweep_down = i_low < ref_low
    sweep = sweep_up or sweep_down

    rejection_up = sweep_up and i_close < ref_high
    rejection_down = sweep_down and i_close > ref_low
    rejection = rejection_up or rejection_down

    range_ratio = (i_range / atr15) if atr15 > 0 else 0.0
    range_anomaly = range_ratio > 1.8

    follow_through_failure = False
    if sweep_up:
        follow_through_failure = f_close <= i_close
    elif sweep_down:
        follow_through_failure = f_close >= i_close
    elif i_close > i_open:
        follow_through_failure = f_close <= i_close
    elif i_close < i_open:
        follow_through_failure = f_close >= i_close

    score = 0
    reasons: list[str] = []

    if sweep:
        score += 25
        reasons.append("Liquidity sweep detected on M15.")
    if rejection:
        score += 25
        reasons.append("Post-sweep rejection closed back inside prior range.")
    if volume_z > 2.0:
        score += 25
        reasons.append("M15 impulse volume is statistically elevated.")
    if range_anomaly:
        score += 20
        reasons.append("Impulse candle range is abnormal versus ATR.")
    if follow_through_failure:
        score += 15
        reasons.append("Follow-through failed on next M15 candle.")

    if (sweep and rejection and volume_z > 2.0) or (range_anomaly and volume_z > 2.0):
        score = max(score, 80)

    score = min(score, 100)
    if score >= 70:
        level = "high"
    elif score >= 40:
        level = "medium"
    else:
        level = "low"

    if not reasons:
        reasons.append("No abnormal manipulation signature on M15.")

    return ManipulationRisk(
        score=score,
        level=level,
        reasons=reasons,
        volume_z=round(volume_z, 3),
        sweep=sweep,
        rejection=rejection,
        range_anomaly=range_anomaly,
        follow_through_failure=follow_through_failure,
    )
