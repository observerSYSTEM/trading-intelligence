from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from app.services.data_provider import Candle


TWELVE_DATA_SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "GBPJPY": "GBP/JPY",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "BTCUSD": "BTC/USD",
}

TWELVE_DATA_TIMEFRAME_MAP = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}

TIMEFRAME_SECONDS = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
}


class TwelveDataCandleProviderError(RuntimeError):
    """Raised when Twelve Data cannot return usable candle data."""


class TwelveDataCandleProvider:
    name = "twelvedata"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | float | None = None,
        symbol_map_json: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("TWELVE_DATA_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("TWELVE_DATA_BASE_URL") or "https://api.twelvedata.com").rstrip("/")
        self.timeout_seconds = max(float(timeout_seconds or os.getenv("TWELVE_DATA_TIMEOUT_SECONDS") or 10), 1.0)
        self.symbol_map = self._load_symbol_map(
            symbol_map_json if symbol_map_json is not None else os.getenv("TWELVE_DATA_SYMBOL_MAP_JSON") or ""
        )
        self.session = session or requests.Session()

    @staticmethod
    def _load_symbol_map(raw: str) -> dict[str, str]:
        symbol_map = dict(TWELVE_DATA_SYMBOL_MAP)
        value = (raw or "").strip()
        if not value:
            return symbol_map
        try:
            data = json.loads(value)
        except ValueError as exc:
            raise TwelveDataCandleProviderError("Invalid TWELVE_DATA_SYMBOL_MAP_JSON.") from exc
        if not isinstance(data, dict):
            raise TwelveDataCandleProviderError("TWELVE_DATA_SYMBOL_MAP_JSON must be a JSON object.")
        for key, mapped in data.items():
            canonical = str(key or "").strip().upper()
            provider_symbol = str(mapped or "").strip()
            if canonical and provider_symbol:
                symbol_map[canonical] = provider_symbol
        return symbol_map

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise TwelveDataCandleProviderError("TWELVE_DATA_API_KEY is required when CANDLE_PROVIDER=twelvedata.")

    def _resolve_symbol(self, symbol: str) -> str:
        canonical = (symbol or "").strip().upper()
        if not canonical:
            raise TwelveDataCandleProviderError("Twelve Data symbol cannot be empty.")
        return self.symbol_map.get(canonical, canonical)

    @staticmethod
    def _resolve_timeframe(timeframe: str) -> str:
        tf = (timeframe or "").strip().upper()
        if tf not in TWELVE_DATA_TIMEFRAME_MAP:
            raise TwelveDataCandleProviderError(f"Unsupported timeframe '{timeframe}' for Twelve Data provider.")
        return TWELVE_DATA_TIMEFRAME_MAP[tf]

    @staticmethod
    def _timeframe_delta(timeframe: str) -> timedelta:
        tf = (timeframe or "").strip().upper()
        return timedelta(seconds=TIMEFRAME_SECONDS.get(tf, 60))

    @staticmethod
    def _format_utc(value: datetime) -> str:
        dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        raw = str(value or "").strip().replace("Z", "+00:00")
        if not raw:
            raise TwelveDataCandleProviderError("Twelve Data candle is missing datetime.")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise TwelveDataCandleProviderError(f"Invalid Twelve Data candle datetime: {value}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        self._require_api_key()
        request_params = dict(params)
        request_params["apikey"] = self.api_key
        request_params["format"] = "JSON"
        try:
            response = self.session.get(
                f"{self.base_url}/time_series",
                params=request_params,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TwelveDataCandleProviderError(
                f"Twelve Data request timed out after {self.timeout_seconds:g}s."
            ) from exc
        except requests.RequestException as exc:
            raise TwelveDataCandleProviderError(f"Twelve Data request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TwelveDataCandleProviderError("Twelve Data returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise TwelveDataCandleProviderError("Twelve Data returned an unexpected payload.")
        if not response.ok or payload.get("status") == "error":
            message = str(payload.get("message") or response.text or "unknown error")
            raise TwelveDataCandleProviderError(
                f"Twelve Data API request failed ({response.status_code}): {message[:500]}"
            )
        return payload

    def _parse_candles(self, payload: dict[str, Any], *, requested_symbol: str, timeframe: str) -> list[Candle]:
        rows = payload.get("values")
        if not isinstance(rows, list):
            raise TwelveDataCandleProviderError("Twelve Data response did not include candle values.")
        provider_symbol = self._resolve_symbol(requested_symbol)
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                candles.append(
                    Candle(
                        symbol=requested_symbol.strip().upper(),
                        timeframe=timeframe.strip().upper(),
                        time_utc=self._parse_datetime(str(row.get("datetime") or "")),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        broker_symbol=provider_symbol,
                        volume=float(row["volume"]) if row.get("volume") is not None else None,
                        source=self.name,
                        complete=True,
                    )
                )
            except KeyError as exc:
                raise TwelveDataCandleProviderError(f"Twelve Data candle is missing field: {exc}") from exc
            except (TypeError, ValueError) as exc:
                raise TwelveDataCandleProviderError("Twelve Data candle contains invalid numeric values.") from exc
        candles.sort(key=lambda candle: candle.time_utc)
        return candles

    def _is_closed(self, candle: Candle, timeframe: str) -> bool:
        return candle.time_utc + self._timeframe_delta(timeframe) <= datetime.now(timezone.utc)

    def get_recent_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        requested_symbol = (symbol or "").strip().upper()
        outputsize = min(max(int(count) + 1, 2), 5000)
        payload = self._request(
            {
                "symbol": self._resolve_symbol(requested_symbol),
                "interval": self._resolve_timeframe(timeframe),
                "outputsize": outputsize,
                "order": "DESC",
                "timezone": "UTC",
            }
        )
        values = payload.get("values")
        if isinstance(values, list) and values:
            payload = {"values": values[1:]}
        candles = self._parse_candles(payload, requested_symbol=requested_symbol, timeframe=timeframe)
        candles = [candle for candle in candles if self._is_closed(candle, timeframe)]
        return candles[-max(int(count), 1) :]

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        candles = self.get_recent_candles(symbol, timeframe, count=1)
        if not candles:
            raise TwelveDataCandleProviderError(f"No closed Twelve Data candle returned for {symbol} {timeframe}.")
        return candles[-1]

    def get_candles_range(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[Candle]:
        requested_symbol = (symbol or "").strip().upper()
        start = start_utc.replace(tzinfo=timezone.utc) if start_utc.tzinfo is None else start_utc.astimezone(timezone.utc)
        end = end_utc.replace(tzinfo=timezone.utc) if end_utc.tzinfo is None else end_utc.astimezone(timezone.utc)
        payload = self._request(
            {
                "symbol": self._resolve_symbol(requested_symbol),
                "interval": self._resolve_timeframe(timeframe),
                "start_date": self._format_utc(start),
                "end_date": self._format_utc(end),
                "outputsize": 5000,
                "order": "ASC",
                "timezone": "UTC",
            }
        )
        candles = self._parse_candles(payload, requested_symbol=requested_symbol, timeframe=timeframe)
        return [candle for candle in candles if start <= candle.time_utc < end and self._is_closed(candle, timeframe)]
