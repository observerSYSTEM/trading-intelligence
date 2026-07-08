from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import requests

from app.services.data_provider import Candle


OANDA_SYMBOL_MAP = {
    "XAUUSD": "XAU_USD",
    "GBPJPY": "GBP_JPY",
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "BTCUSD": "BTC_USD",
}

OANDA_TIMEFRAME_MAP = {
    "M1": "M1",
    "M5": "M5",
    "M15": "M15",
    "H1": "H1",
    "H4": "H4",
    "D1": "D",
}


class OandaCandleProviderError(RuntimeError):
    """Raised when OANDA cannot return usable candle data."""


class OandaCandleProvider:
    name = "oanda"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        env: str | None = None,
        timeout_seconds: int | float | None = None,
        symbol_map_json: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("OANDA_API_KEY") or "").strip()
        self.env = (env if env is not None else os.getenv("OANDA_ENV") or "practice").strip().lower()
        self.timeout_seconds = max(float(timeout_seconds or os.getenv("OANDA_TIMEOUT_SECONDS") or 10), 1.0)
        self.symbol_map = self._load_symbol_map(symbol_map_json if symbol_map_json is not None else os.getenv("OANDA_SYMBOL_MAP_JSON") or "")
        self.session = session or requests.Session()
        self.base_url = "https://api-fxtrade.oanda.com" if self.env == "live" else "https://api-fxpractice.oanda.com"

    @staticmethod
    def _load_symbol_map(raw: str) -> dict[str, str]:
        symbol_map = dict(OANDA_SYMBOL_MAP)
        value = (raw or "").strip()
        if not value:
            return symbol_map
        try:
            data = json.loads(value)
        except ValueError as exc:
            raise OandaCandleProviderError("Invalid OANDA_SYMBOL_MAP_JSON.") from exc
        if not isinstance(data, dict):
            raise OandaCandleProviderError("OANDA_SYMBOL_MAP_JSON must be a JSON object.")
        for key, mapped in data.items():
            canonical = str(key or "").strip().upper()
            provider_symbol = str(mapped or "").strip()
            if canonical and provider_symbol:
                symbol_map[canonical] = provider_symbol
        return symbol_map

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise OandaCandleProviderError("OANDA_API_KEY is required when CANDLE_PROVIDER=oanda.")

    def _resolve_symbol(self, symbol: str) -> str:
        canonical = (symbol or "").strip().upper()
        if not canonical:
            raise OandaCandleProviderError("OANDA symbol cannot be empty.")
        return self.symbol_map.get(canonical, canonical)

    @staticmethod
    def _resolve_timeframe(timeframe: str) -> str:
        tf = (timeframe or "").strip().upper()
        if tf not in OANDA_TIMEFRAME_MAP:
            raise OandaCandleProviderError(f"Unsupported timeframe '{timeframe}' for OANDA provider.")
        return OANDA_TIMEFRAME_MAP[tf]

    @staticmethod
    def _parse_time(value: str) -> datetime:
        raw = str(value or "").strip()
        if not raw:
            raise OandaCandleProviderError("OANDA candle is missing time.")
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if "." in raw:
            head, tail = raw.split(".", 1)
            offset = ""
            if "+" in tail:
                fraction, offset = tail.split("+", 1)
                offset = "+" + offset
            elif "-" in tail:
                fraction, offset = tail.split("-", 1)
                offset = "-" + offset
            else:
                fraction = tail
            raw = f"{head}.{fraction[:6]}{offset}"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise OandaCandleProviderError(f"Invalid OANDA candle time: {value}") from exc
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    def _request(self, instrument: str, params: dict[str, Any]) -> dict[str, Any]:
        self._require_api_key()
        url = f"{self.base_url}/v3/instruments/{instrument}/candles"
        try:
            response = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise OandaCandleProviderError(f"OANDA request timed out after {self.timeout_seconds:g}s.") from exc
        except requests.RequestException as exc:
            raise OandaCandleProviderError(f"OANDA request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise OandaCandleProviderError("OANDA returned invalid JSON.") from exc
        if not isinstance(payload, dict):
            raise OandaCandleProviderError("OANDA returned an unexpected payload.")
        if not response.ok:
            message = str(payload.get("errorMessage") or payload.get("message") or response.text or "unknown error")
            raise OandaCandleProviderError(f"OANDA API request failed ({response.status_code}): {message[:500]}")
        return payload

    def _parse_candles(self, payload: dict[str, Any], *, requested_symbol: str, timeframe: str) -> list[Candle]:
        rows = payload.get("candles")
        if not isinstance(rows, list):
            raise OandaCandleProviderError("OANDA response did not include candles.")
        provider_symbol = self._resolve_symbol(requested_symbol)
        candles: list[Candle] = []
        for row in rows:
            if not isinstance(row, dict) or not bool(row.get("complete")):
                continue
            mid = row.get("mid")
            if not isinstance(mid, dict):
                continue
            try:
                candles.append(
                    Candle(
                        symbol=requested_symbol.strip().upper(),
                        timeframe=timeframe.strip().upper(),
                        time_utc=self._parse_time(str(row.get("time") or "")),
                        open=float(mid["o"]),
                        high=float(mid["h"]),
                        low=float(mid["l"]),
                        close=float(mid["c"]),
                        broker_symbol=provider_symbol,
                        volume=float(row["volume"]) if row.get("volume") is not None else None,
                        source=self.name,
                        complete=True,
                    )
                )
            except KeyError as exc:
                raise OandaCandleProviderError(f"OANDA candle is missing field: {exc}") from exc
            except (TypeError, ValueError) as exc:
                raise OandaCandleProviderError("OANDA candle contains invalid numeric values.") from exc
        candles.sort(key=lambda candle: candle.time_utc)
        return candles

    def get_recent_candles(self, symbol: str, timeframe: str, count: int = 100) -> list[Candle]:
        requested_symbol = (symbol or "").strip().upper()
        instrument = self._resolve_symbol(requested_symbol)
        payload = self._request(
            instrument,
            {
                "price": "M",
                "granularity": self._resolve_timeframe(timeframe),
                "count": min(max(int(count) + 1, 2), 5000),
            },
        )
        candles = self._parse_candles(payload, requested_symbol=requested_symbol, timeframe=timeframe)
        return candles[-max(int(count), 1) :]

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        candles = self.get_recent_candles(symbol, timeframe, count=2)
        if not candles:
            raise OandaCandleProviderError(f"No complete OANDA candle returned for {symbol} {timeframe}.")
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
        instrument = self._resolve_symbol(requested_symbol)
        payload = self._request(
            instrument,
            {
                "price": "M",
                "granularity": self._resolve_timeframe(timeframe),
                "from": self._format_time(start),
                "to": self._format_time(end),
            },
        )
        candles = self._parse_candles(payload, requested_symbol=requested_symbol, timeframe=timeframe)
        return [candle for candle in candles if start <= candle.time_utc < end]
