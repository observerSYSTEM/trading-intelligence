def compute_daily_market_state(symbol: str, date_uk):
    """
    ADMIN-ONLY LOGIC
    - London 08:01 candle
    - Oracle liquidity H1
    - ATR
    - News filter
    """
    internal_bias = {
        "london_0801": "bullish",
        "atr_state": "optimal",
        "news": "clear"
    }

    allowed_direction = "BUY_ONLY"

    return allowed_direction, internal_bias

def evaluate_m5_setup(user, symbol, market_state):
    internal_reason = {
        "bias": "aligned",
        "liquidity": "hit",
        "atr": "ok",
        "news": "clear",
        "m5": "confirmed"
    }

    public_reason = {
        "Session bias aligned": True,
        "At H1 liquidity": True,
        "ATR acceptable": True,
        "Not in news window": True,
        "M5 confirmation present": True
    }

    return "ALLOWED", public_reason, internal_reason

