from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings

TierName = str
StrategyName = str

DAILY_BIAS = "DAILY_BIAS"
LIQ_SWEEP = "LIQ_SWEEP"
NEWS_EXEC = "NEWS_EXEC"
VOL_MANIP = "VOL_MANIP"
ZONE_TO_ZONE = "ZONE_TO_ZONE"

VALID_STRATEGY_NAMES = {
    DAILY_BIAS,
    LIQ_SWEEP,
    NEWS_EXEC,
    VOL_MANIP,
    ZONE_TO_ZONE,
}

TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}

STRATEGY_SYMBOL_MATRIX: dict[StrategyName, set[str]] = {
    DAILY_BIAS: {"XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"},
    LIQ_SWEEP: {"XAUUSD", "GBPUSD"},
    NEWS_EXEC: {"XAUUSD", "GBPUSD"},
    VOL_MANIP: {"XAUUSD"},
    ZONE_TO_ZONE: {"XAUUSD", "GBPUSD"},
}

_ELITE_ONLY_STRATEGIES = {NEWS_EXEC, VOL_MANIP}


@dataclass(frozen=True)
class StrategyMatrixError(ValueError):
    symbol: str
    strategy_name: str
    tier: str
    reason: str

    def __str__(self) -> str:
        return (
            f"Strategy matrix blocked symbol={self.symbol} "
            f"strategy={self.strategy_name} tier={self.tier} reason={self.reason}"
        )


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _normalize_tier(tier: str | None) -> str:
    value = (tier or "basic").strip().lower()
    return value if value in TIER_ORDER else "basic"


def _normalize_strategy(strategy_name: str | None) -> str:
    value = (strategy_name or DAILY_BIAS).strip().upper()
    return value


def _elite_liq_sweep_expansion_enabled() -> bool:
    return bool(getattr(settings, "STRATEGY_MATRIX_ENABLE_ELITE_LIQ_SWEEP_EXPANSION", False))


def _elite_zone_to_zone_expansion_enabled() -> bool:
    return bool(getattr(settings, "STRATEGY_MATRIX_ENABLE_ELITE_ZONE_TO_ZONE_EXPANSION", False))


def _strategy_symbols_for_tier(strategy_name: str, tier: str) -> set[str]:
    base = set(STRATEGY_SYMBOL_MATRIX.get(strategy_name, set()))
    if tier == "elite":
        if strategy_name == LIQ_SWEEP and _elite_liq_sweep_expansion_enabled():
            base.update({"EURUSD", "GBPJPY"})
        if strategy_name == ZONE_TO_ZONE and _elite_zone_to_zone_expansion_enabled():
            base.update({"EURUSD", "GBPJPY"})
    return base


def validate_symbol_for_strategy(symbol: str, strategy_name: str, tier: str) -> bool:
    symbol_value = _normalize_symbol(symbol)
    tier_value = _normalize_tier(tier)
    strategy_value = _normalize_strategy(strategy_name)

    if strategy_value not in VALID_STRATEGY_NAMES:
        raise StrategyMatrixError(
            symbol=symbol_value,
            strategy_name=strategy_value,
            tier=tier_value,
            reason="unknown_strategy",
        )

    if strategy_value in _ELITE_ONLY_STRATEGIES and TIER_ORDER.get(tier_value, 0) < TIER_ORDER["elite"]:
        raise StrategyMatrixError(
            symbol=symbol_value,
            strategy_name=strategy_value,
            tier=tier_value,
            reason="tier_not_allowed",
        )

    allowed_symbols = _strategy_symbols_for_tier(strategy_value, tier_value)
    if symbol_value not in allowed_symbols:
        raise StrategyMatrixError(
            symbol=symbol_value,
            strategy_name=strategy_value,
            tier=tier_value,
            reason="symbol_not_supported",
        )

    return True


def get_active_strategy_matrix() -> dict[str, dict]:
    tiers = ("basic", "pro", "elite")
    strategies: dict[str, dict] = {}
    for strategy_name in sorted(VALID_STRATEGY_NAMES):
        per_tier = {tier: sorted(_strategy_symbols_for_tier(strategy_name, tier)) for tier in tiers}
        strategies[strategy_name] = {
            "elite_only": strategy_name in _ELITE_ONLY_STRATEGIES,
            "symbols_by_tier": per_tier,
        }
    return {
        "strategies": strategies,
        "gates": {
            "elite_liq_sweep_expansion_enabled": _elite_liq_sweep_expansion_enabled(),
            "elite_zone_to_zone_expansion_enabled": _elite_zone_to_zone_expansion_enabled(),
        },
    }

