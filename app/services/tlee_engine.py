from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


try:
    UK_TZ = ZoneInfo("Europe/London")
except ZoneInfoNotFoundError:  # pragma: no cover - tzdata is installed in normal envs.
    UK_TZ = timezone.utc


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_time(candles: list[Any]) -> datetime:
    for candle in reversed(candles):
        value = getattr(candle, "time_utc", None)
        if isinstance(value, datetime):
            return _as_utc(value)
    return datetime.now(timezone.utc)


def evaluate_tlee(candles: list[Any]) -> dict[str, Any]:
    reasons: list[str] = []
    now_london = _latest_time(candles).astimezone(UK_TZ)
    minutes = now_london.hour * 60 + now_london.minute

    london_start = 8 * 60
    london_end = 11 * 60
    ny_start = 13 * 60 + 30
    ny_end = 16 * 60
    silver_start = 15 * 60
    silver_end = 16 * 60

    session = "OFF_SESSION"
    expansion_window = "NONE"
    probability = "LOW"

    if london_start <= minutes < london_end:
        session = "LONDON"
        expansion_window = "LONDON_0800_1100_UK"
        probability = "HIGH" if minutes < 10 * 60 else "MEDIUM"
        reasons.append("Latest candle is inside the London 08:00-11:00 UK expansion window.")
    elif ny_start <= minutes < ny_end:
        session = "NEW_YORK"
        expansion_window = "NY_1330_1600_UK"
        probability = "HIGH" if silver_start <= minutes < silver_end else "MEDIUM"
        reasons.append("Latest candle is inside the New York 13:30-16:00 UK expansion window.")
        if silver_start <= minutes < silver_end:
            expansion_window = "SILVER_BULLET_1500_1600_UK"
            reasons.append("Latest candle is inside the 15:00-16:00 UK Silver Bullet window.")
    else:
        reasons.append("Latest candle is outside the main London and New York expansion windows.")
        if london_start - 60 <= minutes < london_start or ny_start - 60 <= minutes < ny_start:
            probability = "MEDIUM"
            reasons.append("Price is within one hour before a major expansion window.")

    return {
        "session": session,
        "expansion_window": expansion_window,
        "expansion_probability": probability,
        "reason": reasons,
    }


def fallback_tlee(error: str | None = None) -> dict[str, Any]:
    reasons = ["TLEE unavailable; using neutral timing context."]
    if error:
        reasons.append(error)
    return {
        "session": "UNKNOWN",
        "expansion_window": "UNKNOWN",
        "expansion_probability": "LOW",
        "reason": reasons,
    }
