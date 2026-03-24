from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from runner.config import RunnerSettings


@dataclass
class MT5OrderResult:
    status: str
    broker_ticket: str | None = None
    filled_price: float | None = None
    error: str | None = None


class MT5Client:
    def __init__(self, settings: RunnerSettings) -> None:
        self.settings = settings
        self._mt5 = self._load_mt5()
        self._connected = False

    @staticmethod
    def _load_mt5():
        try:
            import MetaTrader5 as mt5  # type: ignore
        except Exception as exc:
            raise RuntimeError("MetaTrader5 package is not installed on runner.") from exc
        return mt5

    def connect(self) -> None:
        if self._connected:
            return

        if self.settings.mt5_terminal_path:
            ok = self._mt5.initialize(path=self.settings.mt5_terminal_path)
        else:
            ok = self._mt5.initialize()
        if not ok:
            raise RuntimeError(f"MT5 initialize failed: {self._mt5.last_error()}")

        login_ok = self._mt5.login(
            login=int(self.settings.mt5_login or 0),
            password=str(self.settings.mt5_password or ""),
            server=str(self.settings.mt5_server or ""),
        )
        if not login_ok:
            raise RuntimeError(f"MT5 login failed: {self._mt5.last_error()}")

        self._connected = True

    def shutdown(self) -> None:
        try:
            self._mt5.shutdown()
        except Exception:
            pass
        self._connected = False

    def _ensure_symbol(self, symbol: str) -> None:
        if not self._mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for {symbol}")

    def _tick_price(self, symbol: str, side: str) -> float:
        tick = self._mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"No tick data for {symbol}")
        if side == "BUY":
            return float(tick.ask)
        return float(tick.bid)

    def execute_job(self, job: dict) -> MT5OrderResult:
        symbol = str(job.get("symbol", "")).strip().upper()
        side = str(job.get("side", "")).strip().upper()
        volume = float(job.get("volume") or 0.0)
        entry_type = str(job.get("entry_type", "MARKET")).strip().upper()
        sl = job.get("sl")
        tp = job.get("tp")
        entry_price = job.get("entry_price")
        job_id = str(job.get("id") or "")
        user_id = str(job.get("user_id") or "")

        if side not in {"BUY", "SELL"}:
            return MT5OrderResult(status="failed", error=f"Unsupported side: {side}")
        if volume <= 0:
            return MT5OrderResult(status="failed", error="Volume must be > 0")

        if self.settings.dry_run:
            return MT5OrderResult(status="filled", broker_ticket=f"dry-{job_id[:8]}", filled_price=float(entry_price or 0.0))

        self.connect()
        self._ensure_symbol(symbol)

        if side == "BUY":
            order_type_market = self._mt5.ORDER_TYPE_BUY
            order_type_limit = self._mt5.ORDER_TYPE_BUY_LIMIT
            order_type_stop = self._mt5.ORDER_TYPE_BUY_STOP
        else:
            order_type_market = self._mt5.ORDER_TYPE_SELL
            order_type_limit = self._mt5.ORDER_TYPE_SELL_LIMIT
            order_type_stop = self._mt5.ORDER_TYPE_SELL_STOP

        comment = f"u:{user_id[:8]}|j:{job_id[:8]}"
        request: dict[str, Any] = {
            "symbol": symbol,
            "volume": volume,
            "sl": float(sl) if sl is not None else 0.0,
            "tp": float(tp) if tp is not None else 0.0,
            "deviation": 20,
            "magic": 880100,
            "comment": comment,
            "type_time": self._mt5.ORDER_TIME_GTC,
            "type_filling": self._mt5.ORDER_FILLING_IOC,
        }

        if entry_type == "MARKET":
            request.update(
                {
                    "action": self._mt5.TRADE_ACTION_DEAL,
                    "type": order_type_market,
                    "price": self._tick_price(symbol, side),
                }
            )
        else:
            pending_price = float(entry_price or 0.0)
            if pending_price <= 0:
                return MT5OrderResult(status="failed", error="Pending order requires entry_price")
            pending_type = order_type_limit if entry_type == "LIMIT" else order_type_stop
            request.update(
                {
                    "action": self._mt5.TRADE_ACTION_PENDING,
                    "type": pending_type,
                    "price": pending_price,
                }
            )

        result = self._mt5.order_send(request)
        if result is None:
            return MT5OrderResult(status="failed", error=f"order_send returned None: {self._mt5.last_error()}")

        retcode = int(getattr(result, "retcode", 0))
        done_codes = {
            int(getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)),
            int(getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008)),
        }
        if retcode not in done_codes:
            return MT5OrderResult(status="failed", error=f"retcode={retcode} comment={getattr(result, 'comment', '')}")

        ticket = str(getattr(result, "order", None) or getattr(result, "deal", None) or "")
        fill_price = float(getattr(result, "price", 0.0) or request.get("price") or 0.0)
        return MT5OrderResult(status="filled", broker_ticket=ticket or None, filled_price=fill_price)

    def get_open_positions(self, *, symbols: list[str] | None = None) -> list[dict]:
        self.connect()
        rows = self._mt5.positions_get()
        if rows is None:
            return []
        allowed = {s.upper() for s in (symbols or [])}
        out: list[dict] = []
        for row in rows:
            symbol = str(getattr(row, "symbol", "")).upper()
            if allowed and symbol not in allowed:
                continue
            side = "BUY" if int(getattr(row, "type", 0)) == int(getattr(self._mt5, "POSITION_TYPE_BUY", 0)) else "SELL"
            out.append(
                {
                    "ticket": str(getattr(row, "ticket", "")),
                    "symbol": symbol,
                    "side": side,
                    "volume": float(getattr(row, "volume", 0.0)),
                    "entry": float(getattr(row, "price_open", 0.0)),
                    "sl": float(getattr(row, "sl", 0.0)) or None,
                    "tp": float(getattr(row, "tp", 0.0)) or None,
                    "pnl": float(getattr(row, "profit", 0.0)),
                    "comment": str(getattr(row, "comment", "")),
                }
            )
        return out

    def get_recent_closed_events(self, *, since_utc: datetime) -> list[dict]:
        self.connect()
        end_utc = datetime.now(timezone.utc)
        rows = self._mt5.history_deals_get(_as_utc(since_utc), end_utc)
        if rows is None:
            return []

        out: list[dict] = []
        tp_reason = int(getattr(self._mt5, "DEAL_REASON_TP", 5))
        sl_reason = int(getattr(self._mt5, "DEAL_REASON_SL", 4))
        out_entry = int(getattr(self._mt5, "DEAL_ENTRY_OUT", 1))
        for row in rows:
            entry = int(getattr(row, "entry", -1))
            if entry != out_entry:
                continue
            reason = int(getattr(row, "reason", -1))
            if reason == tp_reason:
                status = "TP"
                reason_text = "Take profit hit"
            elif reason == sl_reason:
                status = "SL"
                reason_text = "Stop loss hit"
            else:
                status = "CLOSED"
                reason_text = "Position closed"
            out.append(
                {
                    "ticket": str(getattr(row, "position_id", "")),
                    "symbol": str(getattr(row, "symbol", "")).upper(),
                    "status": status,
                    "reason": reason_text,
                    "price": float(getattr(row, "price", 0.0)),
                    "time_utc": datetime.fromtimestamp(int(getattr(row, "time", 0)), tz=timezone.utc).isoformat(),
                    "comment": str(getattr(row, "comment", "")),
                }
            )
        return out


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

