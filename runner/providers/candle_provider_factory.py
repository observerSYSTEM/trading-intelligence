from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Protocol

from app.services.data_provider import Candle
from runner.providers.oanda_candle_provider import OandaCandleProvider
from runner.providers.twelvedata_candle_provider import TwelveDataCandleProvider

logger = logging.getLogger(__name__)


class CandleProvider(Protocol):
    name: str

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        ...

    def get_recent_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        ...

    def get_candles_range(self, symbol: str, timeframe: str, start_utc: datetime, end_utc: datetime) -> list[Candle]:
        ...


def _normalize_provider_name(value: str | None) -> str:
    provider = (value or "").strip().lower().replace("-", "_")
    if provider in {"twelve", "twelve_data"}:
        return "twelvedata"
    return provider


class FallbackCandleProvider:
    name = "api"

    def __init__(self, primary: CandleProvider, fallback: CandleProvider | None = None) -> None:
        self.primary = primary
        self.fallback = fallback
        logger.info(
            "candle_provider_selected primary=%s fallback=%s",
            primary.name,
            fallback.name if fallback is not None else None,
        )

    def _call(self, method_name: str, symbol: str, timeframe: str, *args):
        try:
            result = getattr(self.primary, method_name)(symbol, timeframe, *args)
            logger.info(
                "candle_fetch_ok provider=%s method=%s symbol=%s timeframe=%s",
                self.primary.name,
                method_name,
                symbol,
                timeframe,
            )
            return result
        except Exception as primary_exc:
            logger.warning(
                "candle_fetch_failed provider=%s method=%s symbol=%s timeframe=%s error=%s",
                self.primary.name,
                method_name,
                symbol,
                timeframe,
                primary_exc,
            )
            if self.fallback is None:
                raise
            logger.info(
                "candle_fallback_used primary=%s fallback=%s method=%s symbol=%s timeframe=%s",
                self.primary.name,
                self.fallback.name,
                method_name,
                symbol,
                timeframe,
            )
            try:
                result = getattr(self.fallback, method_name)(symbol, timeframe, *args)
                logger.info(
                    "candle_fetch_ok provider=%s method=%s symbol=%s timeframe=%s",
                    self.fallback.name,
                    method_name,
                    symbol,
                    timeframe,
                )
                return result
            except Exception as fallback_exc:
                logger.warning(
                    "candle_fetch_failed provider=%s method=%s symbol=%s timeframe=%s error=%s",
                    self.fallback.name,
                    method_name,
                    symbol,
                    timeframe,
                    fallback_exc,
                )
                raise

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        return self._call("get_latest_closed_candle", symbol, timeframe)

    def get_recent_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        return self._call("get_recent_candles", symbol, timeframe, count)

    def get_candles_range(self, symbol: str, timeframe: str, start_utc: datetime, end_utc: datetime) -> list[Candle]:
        return self._call("get_candles_range", symbol, timeframe, start_utc, end_utc)


def _build_provider(
    name: str,
    *,
    oanda_api_key: str = "",
    oanda_env: str = "practice",
    oanda_timeout_seconds: int | float = 10,
    oanda_symbol_map_json: str = "",
    twelve_data_api_key: str = "",
    twelve_data_base_url: str = "https://api.twelvedata.com",
    twelve_data_timeout_seconds: int | float = 10,
    twelve_data_symbol_map_json: str = "",
) -> CandleProvider:
    provider = _normalize_provider_name(name)
    if provider == "oanda":
        return OandaCandleProvider(
            api_key=oanda_api_key,
            env=oanda_env,
            timeout_seconds=oanda_timeout_seconds,
            symbol_map_json=oanda_symbol_map_json,
        )
    if provider == "twelvedata":
        return TwelveDataCandleProvider(
            api_key=twelve_data_api_key,
            base_url=twelve_data_base_url,
            timeout_seconds=twelve_data_timeout_seconds,
            symbol_map_json=twelve_data_symbol_map_json,
        )
    raise RuntimeError(f"Unsupported candle provider '{name}'.")


def create_candle_provider(
    *,
    candle_provider: str = "oanda",
    fallback_provider: str = "twelvedata",
    oanda_api_key: str = "",
    oanda_env: str = "practice",
    oanda_timeout_seconds: int | float = 10,
    oanda_symbol_map_json: str = "",
    twelve_data_api_key: str = "",
    twelve_data_base_url: str = "https://api.twelvedata.com",
    twelve_data_timeout_seconds: int | float = 10,
    twelve_data_symbol_map_json: str = "",
) -> FallbackCandleProvider:
    primary_name = _normalize_provider_name(candle_provider) or "oanda"
    fallback_name = _normalize_provider_name(fallback_provider)
    primary = _build_provider(
        primary_name,
        oanda_api_key=oanda_api_key,
        oanda_env=oanda_env,
        oanda_timeout_seconds=oanda_timeout_seconds,
        oanda_symbol_map_json=oanda_symbol_map_json,
        twelve_data_api_key=twelve_data_api_key,
        twelve_data_base_url=twelve_data_base_url,
        twelve_data_timeout_seconds=twelve_data_timeout_seconds,
        twelve_data_symbol_map_json=twelve_data_symbol_map_json,
    )
    fallback = None
    if fallback_name and fallback_name != primary_name:
        fallback = _build_provider(
            fallback_name,
            oanda_api_key=oanda_api_key,
            oanda_env=oanda_env,
            oanda_timeout_seconds=oanda_timeout_seconds,
            oanda_symbol_map_json=oanda_symbol_map_json,
            twelve_data_api_key=twelve_data_api_key,
            twelve_data_base_url=twelve_data_base_url,
            twelve_data_timeout_seconds=twelve_data_timeout_seconds,
            twelve_data_symbol_map_json=twelve_data_symbol_map_json,
        )
    return FallbackCandleProvider(primary=primary, fallback=fallback)


def get_candle_provider_from_env() -> FallbackCandleProvider:
    return create_candle_provider(
        candle_provider=os.getenv("CANDLE_PROVIDER") or "oanda",
        fallback_provider=os.getenv("CANDLE_FALLBACK_PROVIDER") or "twelvedata",
        oanda_api_key=os.getenv("OANDA_API_KEY") or "",
        oanda_env=os.getenv("OANDA_ENV") or "practice",
        oanda_timeout_seconds=float(os.getenv("OANDA_TIMEOUT_SECONDS") or 10),
        oanda_symbol_map_json=os.getenv("OANDA_SYMBOL_MAP_JSON") or "",
        twelve_data_api_key=os.getenv("TWELVE_DATA_API_KEY") or "",
        twelve_data_base_url=os.getenv("TWELVE_DATA_BASE_URL") or "https://api.twelvedata.com",
        twelve_data_timeout_seconds=float(os.getenv("TWELVE_DATA_TIMEOUT_SECONDS") or 10),
        twelve_data_symbol_map_json=os.getenv("TWELVE_DATA_SYMBOL_MAP_JSON") or "",
    )
