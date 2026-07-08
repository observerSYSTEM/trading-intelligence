from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import time
from typing import Any, Callable, Mapping, MutableMapping

from app.core.config import settings

ALL_SYMBOLS: list[str] = ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"]
DEFAULT_SYMBOL = "XAUUSD"
APP_SYMBOL_ENV_SOURCES = ("ORACLE_ENABLED_SYMBOLS", "ORACLE_SYMBOL")
RUNNER_SYMBOL_ENV_SOURCES = ("RUNNER_SYMBOLS", *APP_SYMBOL_ENV_SOURCES)
MT5_SYMBOL_MAP_ENV_SOURCES = ("MT5_SYMBOL_MAP_JSON", "RUNNER_SYMBOL_MAP_JSON")

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


@dataclass(frozen=True)
class ResolvedSymbolConfig:
    symbols: list[str]
    raw_env_value: str | None
    resolved_path: str
    used_fallback: bool


def normalize_symbol(value: str | None) -> str:
    return (value or "").strip().upper()


def normalize_symbol_list(
    values: list[str] | tuple[str, ...] | None,
    *,
    fallback: list[str] | tuple[str, ...] | None = None,
    allowed: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[str]:
    allowed_set = {normalize_symbol(str(item)) for item in (allowed or []) if normalize_symbol(str(item))}
    out: list[str] = []
    for raw in values or []:
        symbol = normalize_symbol(str(raw))
        if not symbol:
            continue
        if allowed is not None and symbol not in allowed_set:
            continue
        if symbol not in out:
            out.append(symbol)
    return out or list(fallback or [])


def normalize_plan(plan: str | None) -> str:
    value = (plan or "basic").strip().lower()
    if value not in TIER_SYMBOLS:
        return "basic"
    return value


def allowed_symbols_for_tier(tier: str | None) -> set[str]:
    return set(TIER_SYMBOLS[normalize_plan(tier)])


def allowed_symbols_for_plan(plan: str | None) -> list[str]:
    allowed = allowed_symbols_for_tier(plan)
    return [symbol for symbol in configured_symbols_from_settings() if symbol in allowed]


def parse_symbols_csv(
    raw: str | None,
    *,
    fallback: list[str] | tuple[str, ...] | None = None,
    allowed: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[str]:
    if not raw:
        return list(fallback or [])
    return normalize_symbol_list(raw.split(","), fallback=fallback, allowed=allowed)


def resolve_symbol_config(
    *,
    env_sources: tuple[str, ...],
    setting_sources: tuple[str, ...] = (),
    fallback: list[str] | tuple[str, ...] | None = None,
    allowed: list[str] | tuple[str, ...] | set[str] | None = None,
    env_getter: Callable[[str], str | None] = os.getenv,
) -> ResolvedSymbolConfig:
    for env_name in env_sources:
        raw = env_getter(env_name)
        parsed = parse_symbols_csv(raw, allowed=allowed)
        if parsed:
            return ResolvedSymbolConfig(
                symbols=parsed,
                raw_env_value=raw,
                resolved_path=env_name,
                used_fallback=False,
            )

    for setting_name in setting_sources:
        raw = getattr(settings, setting_name, None)
        parsed = parse_symbols_csv(raw, allowed=allowed)
        if parsed:
            return ResolvedSymbolConfig(
                symbols=parsed,
                raw_env_value=str(raw) if raw is not None else None,
                resolved_path=f"settings.{setting_name}",
                used_fallback=False,
            )

    fallback_symbols = normalize_symbol_list(list(fallback or []), allowed=allowed)
    if fallback_symbols:
        return ResolvedSymbolConfig(
            symbols=fallback_symbols,
            raw_env_value=None,
            resolved_path=f"default:{','.join(fallback_symbols)}",
            used_fallback=True,
        )

    return ResolvedSymbolConfig(
        symbols=[],
        raw_env_value=None,
        resolved_path="default:empty",
        used_fallback=True,
    )


def configured_symbol_config() -> ResolvedSymbolConfig:
    return resolve_symbol_config(
        env_sources=APP_SYMBOL_ENV_SOURCES,
        setting_sources=APP_SYMBOL_ENV_SOURCES,
        fallback=list(ALL_SYMBOLS),
        allowed=ALL_SYMBOLS,
    )


def runner_symbol_config() -> ResolvedSymbolConfig:
    return resolve_symbol_config(
        env_sources=RUNNER_SYMBOL_ENV_SOURCES,
        fallback=[DEFAULT_SYMBOL],
        allowed=ALL_SYMBOLS,
    )


def configured_symbols_from_settings() -> list[str]:
    return list(configured_symbol_config().symbols)


def enabled_symbols_from_settings() -> list[str]:
    return configured_symbols_from_settings()


def default_configured_symbol(plan: str | None = None) -> str:
    symbols = allowed_symbols_for_plan(plan) if plan is not None else configured_symbols_from_settings()
    return symbols[0] if symbols else DEFAULT_SYMBOL


def parse_symbol_map_json(raw: str | None) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, str] = {}
    for key, value in data.items():
        symbol = normalize_symbol(str(key))
        broker_symbol = str(value).strip()
        if symbol and broker_symbol:
            out[symbol] = broker_symbol
    return out


def configured_symbol_map_from_env(
    *,
    env_sources: tuple[str, ...] = MT5_SYMBOL_MAP_ENV_SOURCES,
    env_getter: Callable[[str], str | None] = os.getenv,
) -> dict[str, str]:
    for env_name in env_sources:
        raw = env_getter(env_name)
        parsed = parse_symbol_map_json(raw)
        if parsed:
            return parsed
    return {}


def resolve_mt5_broker_symbol(
    mt5: Any,
    requested_symbol: str,
    *,
    symbol_map: Mapping[str, str] | None = None,
    cache: MutableMapping[str, str] | None = None,
    on_resolve: Callable[[dict[str, object]], None] | None = None,
) -> str:
    requested = normalize_symbol(requested_symbol)
    if not requested:
        raise RuntimeError("symbol is required")

    tried: list[str] = []
    normalized_map = {
        normalize_symbol(str(key)): str(value).strip()
        for key, value in dict(symbol_map or {}).items()
        if normalize_symbol(str(key)) and str(value).strip()
    }

    def _notify(resolved_symbol: str, source: str) -> None:
        if on_resolve is None:
            return
        on_resolve(
            {
                "requested_symbol": requested,
                "resolved_symbol": resolved_symbol,
                "resolution_source": source,
                "tried": list(tried),
            }
        )

    if cache is not None:
        cached = str(cache.get(requested) or "").strip()
        if cached:
            tried.append(cached)
            if mt5.symbol_select(cached, True):
                _notify(cached, "cache")
                return cached

    candidates: list[tuple[str, str]] = []
    mapped = normalized_map.get(requested)
    if mapped:
        candidates.append((mapped, "configured_map"))
    candidates.extend(
        [
            (requested, "exact"),
            (f"{requested}m", "suffix_guess"),
            (f"{requested}.m", "suffix_guess"),
            (f"{requested}.", "suffix_guess"),
            (f"{requested}_", "suffix_guess"),
        ]
    )

    for candidate, source in candidates:
        broker_symbol = str(candidate or "").strip()
        if not broker_symbol or broker_symbol in tried:
            continue
        tried.append(broker_symbol)
        if mt5.symbol_select(broker_symbol, True):
            if cache is not None:
                cache[requested] = broker_symbol
            _notify(broker_symbol, source)
            return broker_symbol

    try:
        matches = mt5.symbols_get(f"{requested}*") or []
    except Exception:
        matches = []

    for match in matches:
        broker_symbol = str(getattr(match, "name", "")).strip()
        if not broker_symbol or broker_symbol in tried:
            continue
        tried.append(broker_symbol)
        if mt5.symbol_select(broker_symbol, True):
            if cache is not None:
                cache[requested] = broker_symbol
            _notify(broker_symbol, "broker_search")
            return broker_symbol

    raise RuntimeError(f"symbol_select failed for {requested}: {mt5.last_error()} tried={tried}")


def get_symbol_market_config(symbol: str | None) -> SymbolMarketConfig | None:
    symbol_value = normalize_symbol(symbol)
    if not symbol_value:
        return None
    return SYMBOL_MARKET_CONFIGS.get(symbol_value)
