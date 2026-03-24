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
    volume: float | None = None


class DataProvider(Protocol):
    name: str

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        """Return the latest fully closed candle for symbol/timeframe."""

    def get_candles_range(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[Candle]:
        """Return closed candles in [start_utc, end_utc)."""


def get_data_provider() -> DataProvider:
    provider = (settings.MARKET_DATA_PROVIDER or "mt5").strip().lower()

    if provider == "mt5":
        from app.services.mt5_provider import MT5Provider

        return MT5Provider(
            terminal_path=settings.MT5_TERMINAL_PATH,
            login=settings.MT5_LOGIN,
            password=settings.MT5_PASSWORD,
            server=settings.MT5_SERVER,
        )

    raise RuntimeError(f"Unsupported MARKET_DATA_PROVIDER='{settings.MARKET_DATA_PROVIDER}'")
