from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from typing import Any

from app.services.data_provider import Candle


class MT5Provider:
    name = "mt5"

    def __init__(
        self,
        terminal_path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
    ) -> None:
        self.terminal_path = terminal_path
        self.login_id = login
        self.password = password
        self.server = server
        self.mt5 = self._load_mt5_module()

    def _load_mt5_module(self) -> Any:
        try:
            return import_module("MetaTrader5")
        except Exception as exc:
            raise RuntimeError(
                "MetaTrader5 package is not installed. Install with: pip install MetaTrader5"
            ) from exc

    def _resolve_timeframe(self, timeframe: str) -> int:
        tf = (timeframe or "").strip().upper()
        mapping = {
            "M1": self.mt5.TIMEFRAME_M1,
            "M5": self.mt5.TIMEFRAME_M5,
            "M15": self.mt5.TIMEFRAME_M15,
            "M30": self.mt5.TIMEFRAME_M30,
            "H1": self.mt5.TIMEFRAME_H1,
            "H4": self.mt5.TIMEFRAME_H4,
            "D1": self.mt5.TIMEFRAME_D1,
        }
        if tf not in mapping:
            raise RuntimeError(f"Unsupported timeframe '{timeframe}' for MT5 provider")
        return mapping[tf]

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _initialize(self) -> None:
        if self.terminal_path:
            ok = self.mt5.initialize(path=self.terminal_path)
        else:
            ok = self.mt5.initialize()

        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {self.mt5.last_error()}")

        if self.login_id:
            ok = self.mt5.login(
                login=int(self.login_id),
                password=self.password or "",
                server=self.server or "",
            )
            if not ok:
                raise RuntimeError(f"MT5 login failed: {self.mt5.last_error()}")

    def get_latest_closed_candle(self, symbol: str, timeframe: str) -> Candle:
        tf_const = self._resolve_timeframe(timeframe)

        try:
            self._initialize()

            if not self.mt5.symbol_select(symbol, True):
                raise RuntimeError(f"MT5 symbol_select failed for '{symbol}': {self.mt5.last_error()}")

            # start_pos=1 returns the latest fully closed bar (0 is the still-forming bar)
            rates = self.mt5.copy_rates_from_pos(symbol, tf_const, 1, 1)
            if rates is None or len(rates) == 0:
                raise RuntimeError(f"No MT5 candle returned for {symbol} {timeframe}")

            row = rates[0]
            candle_time = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)

            return Candle(
                symbol=symbol,
                timeframe=timeframe.upper(),
                time_utc=candle_time,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["tick_volume"]),
            )
        finally:
            try:
                self.mt5.shutdown()
            except Exception:
                pass

    def get_candles_range(
        self,
        symbol: str,
        timeframe: str,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[Candle]:
        tf_const = self._resolve_timeframe(timeframe)
        start = self._as_utc(start_utc)
        end = self._as_utc(end_utc)

        try:
            self._initialize()

            if not self.mt5.symbol_select(symbol, True):
                raise RuntimeError(f"MT5 symbol_select failed for '{symbol}': {self.mt5.last_error()}")

            rates = self.mt5.copy_rates_range(symbol, tf_const, start, end)
            if rates is None:
                raise RuntimeError(f"MT5 copy_rates_range failed for {symbol} {timeframe}: {self.mt5.last_error()}")

            candles: list[Candle] = []
            for row in rates:
                candle_time = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=timeframe.upper(),
                        time_utc=candle_time,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["tick_volume"]),
                    )
                )
            candles.sort(key=lambda c: c.time_utc)
            return candles
        finally:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
