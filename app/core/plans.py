import os

PLAN_ORDER = {"basic": 1, "pro": 2, "elite": 3}

PRICE_TO_PLAN = {
    os.getenv("STRIPE_PRICE_ID_BASIC"): "basic",
    os.getenv("STRIPE_PRICE_ID_PRO"): "pro",
    os.getenv("STRIPE_PRICE_ID_ELITE"): "elite",
}

def plan_from_price_id(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return PRICE_TO_PLAN.get(price_id)
