from __future__ import annotations

from app.core.config import settings

ALL_SYMBOLS: list[str] = ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"]

TIER_SYMBOLS: dict[str, set[str]] = {
    "basic": {"XAUUSD"},
    "pro": {"XAUUSD", "GBPUSD", "EURUSD"},
    "elite": {"XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"},
}


def normalize_plan(plan: str | None) -> str:
    value = (plan or "basic").strip().lower()
    if value not in TIER_SYMBOLS:
        return "basic"
    return value


def allowed_symbols_for_tier(tier: str | None) -> set[str]:
    return set(TIER_SYMBOLS[normalize_plan(tier)])


def allowed_symbols_for_plan(plan: str | None) -> list[str]:
    allowed = allowed_symbols_for_tier(plan)
    return [symbol for symbol in ALL_SYMBOLS if symbol in allowed]


def parse_symbols_csv(raw: str | None, *, fallback: list[str] | None = None) -> list[str]:
    if not raw:
        return list(fallback or [])
    symbols: list[str] = []
    for item in raw.split(","):
        symbol = item.strip().upper()
        if not symbol:
            continue
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols or list(fallback or [])


def enabled_symbols_from_settings() -> list[str]:
    default = list(ALL_SYMBOLS)
    configured = parse_symbols_csv(settings.ORACLE_ENABLED_SYMBOLS, fallback=default)
    valid = [s for s in configured if s in set(ALL_SYMBOLS)]
    return valid or default
