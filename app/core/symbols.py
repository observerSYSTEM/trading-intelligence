from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from app.core.config import settings

ALL_SYMBOLS: list[str] = ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"]

TIER_SYMBOLS: dict[str, set[str]] = {
    "basic": {"XAUUSD"},
    "pro": {"XAUUSD", "GBPUSD", "EURUSD"},
    "elite": {"XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"},
}


@dataclass(frozen=True)
class SymbolSessionWindow:
    start: time
    end: time


@dataclass(frozen=True)
class SymbolAtrPlaceholder:
    period: int
    enabled: bool = False


@dataclass(frozen=True)
class SymbolAnchorConfig:
    london_open_time: time
    acceptance_body_ratio_min: float
    rejection_wick_ratio_min: float
    strong_body_ratio_min: float
    strong_wick_ratio_min: float


@dataclass(frozen=True)
class SymbolSweepQualityConfig:
    moderate_buffer_pips: float
    strong_buffer_pips: float


@dataclass(frozen=True)
class SymbolSweepConfig:
    minimum_buffer_pips: float
    lookback_bars: int
    quality: SymbolSweepQualityConfig


@dataclass(frozen=True)
class SymbolMagnetRankingConfig:
    buyside: tuple[str, ...]
    sellside: tuple[str, ...]


@dataclass(frozen=True)
class SymbolMagnetConfig:
    round_number_interval: float
    h1_swing_lookback: int
    ranking: SymbolMagnetRankingConfig


@dataclass(frozen=True)
class SymbolStructureQualityConfig:
    moderate_displacement_pips: float
    strong_displacement_pips: float


@dataclass(frozen=True)
class SymbolStructureConfig:
    swing_lookback: int
    break_confirmation_method: str
    minimum_displacement_pips: float
    quality: SymbolStructureQualityConfig


@dataclass(frozen=True)
class SymbolFvgQualityConfig:
    moderate_gap_pips: float
    strong_gap_pips: float


@dataclass(frozen=True)
class SymbolFvgMitigationConfig:
    partial_fill_ratio: float
    full_fill_ratio: float


@dataclass(frozen=True)
class SymbolFvgConfig:
    minimum_gap_size_pips: float
    maximum_age_bars: int
    quality: SymbolFvgQualityConfig
    mitigation: SymbolFvgMitigationConfig


@dataclass(frozen=True)
class SymbolMarketConfig:
    symbol: str
    pip_size: float
    point_scale: int
    asia_session: SymbolSessionWindow
    london_session: SymbolSessionWindow
    new_york_session: SymbolSessionWindow
    asian_range_min_pips: float
    asian_range_max_pips: float
    atr_h1: SymbolAtrPlaceholder
    atr_d1: SymbolAtrPlaceholder
    anchor: SymbolAnchorConfig
    sweep: SymbolSweepConfig
    magnet: SymbolMagnetConfig
    structure: SymbolStructureConfig
    fvg: SymbolFvgConfig


SYMBOL_MARKET_CONFIGS: dict[str, SymbolMarketConfig] = {
    "GBPJPY": SymbolMarketConfig(
        symbol="GBPJPY",
        pip_size=0.01,
        point_scale=10,
        asia_session=SymbolSessionWindow(start=time(0, 0, 0), end=time(6, 59, 59)),
        london_session=SymbolSessionWindow(start=time(7, 0, 0), end=time(11, 0, 0)),
        new_york_session=SymbolSessionWindow(start=time(13, 30, 0), end=time(17, 0, 0)),
        asian_range_min_pips=20.0,
        asian_range_max_pips=80.0,
        atr_h1=SymbolAtrPlaceholder(period=14, enabled=False),
        atr_d1=SymbolAtrPlaceholder(period=14, enabled=False),
        anchor=SymbolAnchorConfig(
            london_open_time=time(8, 1, 0),
            acceptance_body_ratio_min=0.55,
            rejection_wick_ratio_min=0.60,
            strong_body_ratio_min=0.70,
            strong_wick_ratio_min=0.75,
        ),
        sweep=SymbolSweepConfig(
            minimum_buffer_pips=3.0,
            lookback_bars=3,
            quality=SymbolSweepQualityConfig(
                moderate_buffer_pips=5.0,
                strong_buffer_pips=10.0,
            ),
        ),
        magnet=SymbolMagnetConfig(
            round_number_interval=0.5,
            h1_swing_lookback=12,
            ranking=SymbolMagnetRankingConfig(
                buyside=("pdh", "london_high", "asian_high", "h1_swing_high", "round_number"),
                sellside=("pdl", "london_low", "asian_low", "h1_swing_low", "round_number"),
            ),
        ),
        structure=SymbolStructureConfig(
            swing_lookback=10,
            break_confirmation_method="close_beyond_level",
            minimum_displacement_pips=3.0,
            quality=SymbolStructureQualityConfig(
                moderate_displacement_pips=5.0,
                strong_displacement_pips=10.0,
            ),
        ),
        fvg=SymbolFvgConfig(
            minimum_gap_size_pips=3.0,
            maximum_age_bars=8,
            quality=SymbolFvgQualityConfig(
                moderate_gap_pips=5.0,
                strong_gap_pips=10.0,
            ),
            mitigation=SymbolFvgMitigationConfig(
                partial_fill_ratio=0.5,
                full_fill_ratio=1.0,
            ),
        ),
    )
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


def get_symbol_market_config(symbol: str | None) -> SymbolMarketConfig | None:
    symbol_value = (symbol or "").strip().upper()
    if not symbol_value:
        return None
    return SYMBOL_MARKET_CONFIGS.get(symbol_value)
