from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    UK_TZ = ZoneInfo("Europe/London")
except Exception:
    # Fallback: UTC (won't crash)
    from datetime import timezone as _tz
    UK_TZ = _tz.utc


@dataclass
class OracleDecision:
    symbol: str
    date_uk: str  # YYYY-MM-DD
    direction: str  # BUY_ONLY | SELL_ONLY | NO_TRADE
    reason: str


def oracle_from_candle(symbol: str, o: float, h: float, l: float, c: float) -> OracleDecision:
    now_uk = datetime.now(UK_TZ)
    date_uk = now_uk.date().isoformat()

    body = abs(c - o)

    # Basic doji filter (tiny body) -> NO_TRADE
    # You can tighten later with ATR, etc.
    if body <= 0:
        direction = "NO_TRADE"
        reason = "Doji/zero body"
    elif c > o:
        direction = "BUY_ONLY"
        reason = "Candle closed bullish"
    elif c < o:
        direction = "SELL_ONLY"
        reason = "Candle closed bearish"
    else:
        direction = "NO_TRADE"
        reason = "Indecision candle"

    return OracleDecision(symbol=symbol, date_uk=date_uk, direction=direction, reason=reason)
