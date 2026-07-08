from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.config import settings


@dataclass
class Candle:
    symbol: str
    timeframe: str
    time_utc: datetime
    open: float
    high: float
    low: float
    close: float
    broker_symbol: str | None = None
    volume: float | None = None
    source: str | None = None
    complete: bool = True


class DataProvider(Protocol):
    name: str

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        """Return the latest fully closed candle for symbol/timeframe."""

    def get_recent_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        """Return recent fully closed candles for symbol/timeframe."""

    def get_candles_range(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[Candle]:
        """Return closed candles in [start_utc, end_utc)."""


def configured_provider_name() -> str:
    if mt5_disabled():
        return "api"

    provider = (settings.DATA_PROVIDER or settings.MARKET_DATA_PROVIDER or "mt5").strip().lower()
    if provider in {"oanda", "api_candles", "candle_api"}:
        return "api"
    if provider in {"twelve_data", "twelve-data", "twelve"}:
        return "twelvedata"
    return provider


def mt5_disabled() -> bool:
    return bool(settings.DISABLE_MT5)


def api_candle_mode() -> bool:
    return mt5_disabled() or configured_provider_name() in {"api", "twelvedata"}


def candle_provider_debug_labels(
    *,
    latest_candle_source: str | None = None,
    last_candle_time: str | None = None,
    anchor_candle_source: str | None = None,
    anchor_candle_status: str | None = None,
) -> dict:
    return {
        "data_provider": configured_provider_name(),
        "candle_provider": (settings.CANDLE_PROVIDER or "").strip().lower() or None,
        "fallback_provider": (settings.CANDLE_FALLBACK_PROVIDER or "").strip().lower() or None,
        "news_provider": (settings.NEWS_PROVIDER or "").strip().lower() or None,
        "latest_candle_source": latest_candle_source,
        "last_candle_time": last_candle_time,
        "anchor_candle_source": anchor_candle_source,
        "anchor_candle_status": anchor_candle_status,
    }


def get_data_provider() -> DataProvider:
    provider = configured_provider_name()

    if provider == "finnhub":
        raise RuntimeError(
            "DATA_PROVIDER=finnhub provides news/calendar data only. "
            "Set DATA_PROVIDER=api for OHLC candle ingestion."
        )
    if provider == "api" or (provider == "mt5" and mt5_disabled()):
        from runner.providers.candle_provider_factory import create_candle_provider

        return create_candle_provider(
            candle_provider=settings.CANDLE_PROVIDER,
            fallback_provider=settings.CANDLE_FALLBACK_PROVIDER,
            oanda_api_key=settings.OANDA_API_KEY,
            oanda_env=settings.OANDA_ENV,
            oanda_timeout_seconds=settings.OANDA_TIMEOUT_SECONDS,
            oanda_symbol_map_json=settings.OANDA_SYMBOL_MAP_JSON,
            twelve_data_api_key=settings.TWELVE_DATA_API_KEY,
            twelve_data_base_url=settings.TWELVE_DATA_BASE_URL,
            twelve_data_timeout_seconds=settings.TWELVE_DATA_TIMEOUT_SECONDS,
            twelve_data_symbol_map_json=settings.TWELVE_DATA_SYMBOL_MAP_JSON,
        )

    if provider == "mt5":
        if mt5_disabled():
            raise RuntimeError("MT5 data provider is disabled by DISABLE_MT5=true.")
        from app.services.mt5_provider import MT5Provider

        return MT5Provider(
            terminal_path=settings.MT5_TERMINAL_PATH,
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )

    if provider == "twelvedata":
        from runner.providers.candle_provider_factory import create_candle_provider

        return create_candle_provider(
            candle_provider="twelvedata",
            fallback_provider="",
            twelve_data_api_key=settings.TWELVE_DATA_API_KEY,
            twelve_data_base_url=settings.TWELVE_DATA_BASE_URL,
            twelve_data_timeout_seconds=settings.TWELVE_DATA_TIMEOUT_SECONDS,
            twelve_data_symbol_map_json=settings.TWELVE_DATA_SYMBOL_MAP_JSON,
        )

    raise RuntimeError(
        "Unsupported data provider configuration: "
        f"DATA_PROVIDER='{settings.DATA_PROVIDER}', "
        f"MARKET_DATA_PROVIDER='{settings.MARKET_DATA_PROVIDER}', "
        f"CANDLE_PROVIDER='{settings.CANDLE_PROVIDER}', "
        f"DISABLE_MT5={settings.DISABLE_MT5}"
    )
