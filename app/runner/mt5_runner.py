from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_TIMEFRAMES = ("M1", "M15", "H1")
SUPPORTED_TIMEFRAMES = {"M1", "M5", "M15", "M30", "H1", "H4", "D1"}


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _env_bool(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_broker_offset_minutes(raw: str | None) -> int:
    value = (raw or "").strip()
    if not value:
        return 0
    sign = 1
    if value.startswith("-"):
        sign = -1
        value = value[1:]
    elif value.startswith("+"):
        value = value[1:]
    if ":" in value:
        hh_raw, mm_raw = value.split(":", 1)
    else:
        hh_raw, mm_raw = value, "0"
    try:
        hours = int(hh_raw)
        minutes = int(mm_raw)
    except ValueError as exc:
        raise RuntimeError(f"Invalid RUNNER_BROKER_UTC_OFFSET format: {raw}") from exc
    if minutes < 0 or minutes > 59:
        raise RuntimeError(f"Invalid RUNNER_BROKER_UTC_OFFSET minutes: {raw}")
    return sign * (abs(hours) * 60 + minutes)


def _resolve_tz(name: str) -> timezone | ZoneInfo:
    tz_name = (name or "Europe/London").strip() or "Europe/London"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _emit(event: str, payload: dict | None = None) -> None:
    data = {"event": event, "ts_utc": datetime.now(timezone.utc).isoformat()}
    if payload:
        data.update(payload)
    print(json.dumps(data), flush=True)


def _parse_csv(raw: str | None, *, fallback: list[str]) -> list[str]:
    if not raw:
        return list(fallback)
    out: list[str] = []
    for part in raw.split(","):
        value = part.strip().upper()
        if value and value not in out:
            out.append(value)
    return out or list(fallback)


def _parse_timeframes() -> list[str]:
    raw = os.getenv("RUNNER_TIMEFRAMES", ",".join(DEFAULT_TIMEFRAMES))
    out: list[str] = []
    for value in raw.split(","):
        tf = value.strip().upper()
        if tf in SUPPORTED_TIMEFRAMES and tf not in out:
            out.append(tf)
    return out or list(DEFAULT_TIMEFRAMES)


def _parse_symbols() -> list[str]:
    raw = os.getenv("RUNNER_SYMBOLS") or os.getenv("ORACLE_ENABLED_SYMBOLS") or "XAUUSD"
    out: list[str] = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return out or ["XAUUSD"]


def _parse_symbol_map(raw: str | None) -> dict[str, str]:
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
        k = str(key).strip().upper()
        v = str(value).strip()
        if k and v:
            out[k] = v
    return out


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required.")
    return value


def _validate_env() -> dict:
    api_base = ((os.getenv("API_BASE") or os.getenv("APP_URL") or "").strip()).rstrip("/")
    if not api_base:
        raise RuntimeError("API_BASE (or APP_URL) is required.")
    runner_key = _required_env("RUNNER_API_KEY")
    mt5_path = _required_env("MT5_TERMINAL_PATH")
    mt5_login = _required_env("MT5_LOGIN")
    mt5_password = _required_env("MT5_PASSWORD")
    mt5_server = _required_env("MT5_SERVER")
    if not os.path.exists(mt5_path):
        raise RuntimeError(f"MT5_TERMINAL_PATH does not exist: {mt5_path}")

    return {
        "api_base": api_base,
        "runner_key": runner_key,
        "mt5_path": mt5_path,
        "mt5_login": mt5_login,
        "mt5_password": mt5_password,
        "mt5_server": mt5_server,
        "runner_id": (os.getenv("RUNNER_ID") or "mt5-runner").strip(),
        "runner_version": (os.getenv("RUNNER_VERSION") or "1.0.0").strip(),
        "request_timeout_seconds": max(int(os.getenv("RUNNER_REQUEST_TIMEOUT_SECONDS", "30")), 5),
        "loop_seconds": max(int(os.getenv("RUNNER_INTERVAL_SECONDS", "60")), 10),
        "heartbeat_seconds": max(int(os.getenv("RUNNER_HEARTBEAT_SECONDS", "30")), 10),
        "conn_check_seconds": max(int(os.getenv("RUNNER_CONN_CHECK_SECONDS", "20")), 5),
        "control_enabled": _env_bool(os.getenv("RUNNER_CONTROL_ENABLED"), default=True),
        "control_host": (os.getenv("RUNNER_CONTROL_BIND") or "127.0.0.1").strip(),
        "control_port": int(os.getenv("RUNNER_CONTROL_PORT", "8787")),
        "control_require_key": _env_bool(os.getenv("RUNNER_CONTROL_REQUIRE_KEY"), default=True),
        "broker_utc_offset_minutes": _parse_broker_offset_minutes(os.getenv("RUNNER_BROKER_UTC_OFFSET")),
        "london_tz": _resolve_tz(os.getenv("RUNNER_LONDON_TZ") or "Europe/London"),
        "london_tz_name": (os.getenv("RUNNER_LONDON_TZ") or "Europe/London").strip() or "Europe/London",
    }


class RunnerState:
    def __init__(self, *, symbols: list[str]) -> None:
        self._lock = threading.Lock()
        self.mt5_initialized = False
        self.mt5_logged_in = False
        self.account: dict = {}
        self.terminal: dict = {}
        self.server_time_utc: datetime | None = None
        self.last_error: str | None = None
        self.last_success_utc: datetime | None = None
        self.last_ingest_utc: datetime | None = None
        self.last_signal_utc: datetime | None = None
        self.last_telegram_sent_utc: datetime | None = None
        self.last_heartbeat_error: str | None = None
        self.reconnect_requested = False
        self.reconnect_reason: str | None = None
        self.symbols: dict[str, dict] = {
            symbol: {
                "selected": False,
                "broker_symbol": symbol,
                "last_tick_utc": None,
                "last_success_utc": None,
                "last_error": None,
            }
            for symbol in symbols
        }

    def set_connection(
        self,
        *,
        initialized: bool,
        logged_in: bool,
        account: dict | None = None,
        terminal: dict | None = None,
        last_error: str | None = None,
    ) -> None:
        with self._lock:
            self.mt5_initialized = initialized
            self.mt5_logged_in = logged_in
            self.account = account or {}
            self.terminal = terminal or {}
            self.last_error = last_error
            if initialized and logged_in and not last_error:
                self.last_success_utc = datetime.now(timezone.utc)

    def set_symbol_selected(self, *, symbol: str, selected: bool, broker_symbol: str | None = None) -> None:
        sym = symbol.strip().upper()
        with self._lock:
            row = self.symbols.setdefault(
                sym,
                {
                    "selected": False,
                    "broker_symbol": sym,
                    "last_tick_utc": None,
                    "last_success_utc": None,
                    "last_error": None,
                },
            )
            row["selected"] = bool(selected)
            if broker_symbol:
                row["broker_symbol"] = broker_symbol
            if selected:
                row["last_error"] = None

    def set_symbol_tick(self, *, symbol: str, tick_utc: datetime | None) -> None:
        sym = symbol.strip().upper()
        with self._lock:
            row = self.symbols.get(sym)
            if not row:
                return
            row["last_tick_utc"] = tick_utc
            row["last_success_utc"] = datetime.now(timezone.utc)
            row["last_error"] = None
            if tick_utc is not None:
                self.server_time_utc = tick_utc
            self.last_success_utc = datetime.now(timezone.utc)

    def set_symbol_error(self, *, symbol: str, error: str) -> None:
        sym = symbol.strip().upper()
        with self._lock:
            row = self.symbols.setdefault(
                sym,
                {
                    "selected": False,
                    "broker_symbol": sym,
                    "last_tick_utc": None,
                    "last_success_utc": None,
                    "last_error": None,
                },
            )
            row["last_error"] = error[:500]
            self.last_error = error[:1000]

    def mark_ingest_success(self, *, candle_time_utc: datetime) -> None:
        with self._lock:
            self.last_ingest_utc = candle_time_utc
            self.last_signal_utc = candle_time_utc
            self.last_success_utc = datetime.now(timezone.utc)
            self.last_error = None

    def mark_error(self, error: str) -> None:
        with self._lock:
            self.last_error = error[:1000]

    def request_reconnect(self, reason: str) -> None:
        with self._lock:
            self.reconnect_requested = True
            self.reconnect_reason = reason[:200]

    def pop_reconnect(self) -> str | None:
        with self._lock:
            if not self.reconnect_requested:
                return None
            self.reconnect_requested = False
            reason = self.reconnect_reason
            self.reconnect_reason = None
            return reason

    def set_heartbeat_error(self, error: str | None) -> None:
        with self._lock:
            self.last_heartbeat_error = error[:1000] if error else None

    def snapshot(self) -> dict:
        with self._lock:
            symbols_payload: dict[str, dict] = {}
            last_tick_utc: datetime | None = None
            for symbol, row in self.symbols.items():
                tick_value = row.get("last_tick_utc")
                if isinstance(tick_value, datetime):
                    last_tick_utc = tick_value if last_tick_utc is None else max(last_tick_utc, tick_value)
                symbols_payload[symbol] = {
                    "selected": bool(row.get("selected")),
                    "broker_symbol": row.get("broker_symbol"),
                    "last_tick_utc": _iso(row.get("last_tick_utc")),
                    "last_success_utc": _iso(row.get("last_success_utc")),
                    "last_error": row.get("last_error"),
                }
            return {
                "runner_ok": True,
                "mt5_initialized": bool(self.mt5_initialized),
                "mt5_logged_in": bool(self.mt5_logged_in),
                "account": dict(self.account) if self.account else None,
                "terminal": dict(self.terminal) if self.terminal else None,
                "server_time_utc": _iso(self.server_time_utc),
                "last_tick_utc": _iso(last_tick_utc),
                "last_success_utc": _iso(self.last_success_utc),
                "last_ingest_utc": _iso(self.last_ingest_utc),
                "last_signal_utc": _iso(self.last_signal_utc),
                "last_telegram_sent_utc": _iso(self.last_telegram_sent_utc),
                "last_error": self.last_error,
                "last_heartbeat_error": self.last_heartbeat_error,
                "symbols": symbols_payload,
            }


def _json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _build_control_handler(
    *,
    state: RunnerState,
    runner_key: str,
    require_key: bool,
):
    class ControlHandler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            if not require_key:
                return True
            provided = (self.headers.get("X-Runner-Key") or "").strip()
            return bool(provided and provided == runner_key)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/health":
                _json_response(self, 404, {"ok": False, "error": "not_found"})
                return
            if not self._authorized():
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            _json_response(self, 200, state.snapshot())

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != "/reconnect":
                _json_response(self, 404, {"ok": False, "error": "not_found"})
                return
            if not self._authorized():
                _json_response(self, 401, {"ok": False, "error": "unauthorized"})
                return
            body_raw = b""
            content_length = int(self.headers.get("Content-Length") or "0")
            if content_length > 0:
                body_raw = self.rfile.read(min(content_length, 4096))
            reason = "manual"
            if body_raw:
                try:
                    parsed_json = json.loads(body_raw.decode("utf-8"))
                    if isinstance(parsed_json, dict) and isinstance(parsed_json.get("reason"), str):
                        reason = parsed_json["reason"].strip() or reason
                except Exception:
                    pass
            query = parse_qs(parsed.query or "")
            if "reason" in query and query["reason"]:
                reason = str(query["reason"][0]).strip() or reason
            state.request_reconnect(reason)
            _emit("runner.control.reconnect_requested", {"reason": reason})
            _json_response(self, 200, {"ok": True, "reconnect_requested": True, "reason": reason})

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return ControlHandler


def _start_control_server(
    *,
    state: RunnerState,
    runner_key: str,
    host: str,
    port: int,
    require_key: bool,
) -> ThreadingHTTPServer:
    handler_cls = _build_control_handler(state=state, runner_key=runner_key, require_key=require_key)
    server = ThreadingHTTPServer((host, port), handler_cls)
    thread = threading.Thread(target=server.serve_forever, name="runner-control-api", daemon=True)
    thread.start()
    _emit(
        "runner.control.started",
        {
            "host": host,
            "port": port,
            "require_key": require_key,
        },
    )
    return server


class MT5Manager:
    def __init__(
        self,
        *,
        state: RunnerState,
        symbols: list[str],
        mt5_path: str,
        login: int,
        password: str,
        server: str,
        symbol_map: dict[str, str],
        broker_offset_minutes: int = 0,
        london_tz: timezone | ZoneInfo = timezone.utc,
    ) -> None:
        self.state = state
        self.symbols = symbols
        self.mt5_path = mt5_path
        self.login = login
        self.password = password
        self.server = server
        self.symbol_map = symbol_map
        self.broker_offset_minutes = int(broker_offset_minutes)
        self.broker_tz = timezone(timedelta(minutes=self.broker_offset_minutes))
        self.london_tz = london_tz
        self.mt5 = self._load_mt5()
        self.connected = False
        self._broker_symbols: dict[str, str] = {}

    @staticmethod
    def _load_mt5():
        try:
            return import_module("MetaTrader5")
        except Exception as exc:
            raise RuntimeError("MetaTrader5 package is not installed on runner machine.") from exc

    def _last_error_text(self) -> str:
        try:
            return str(self.mt5.last_error())
        except Exception:
            return "unknown_mt5_error"

    def _to_info_dict(self, obj, fields: list[str]) -> dict:
        out: dict = {}
        if obj is None:
            return out
        for field in fields:
            value = getattr(obj, field, None)
            if value is not None:
                out[field] = value
        return out

    def disconnect(self) -> None:
        try:
            self.mt5.shutdown()
        except Exception:
            pass
        self.connected = False
        self.state.set_connection(initialized=False, logged_in=False, account=None, terminal=None, last_error=None)

    def connect(self, *, force: bool = False) -> bool:
        if force:
            self.disconnect()
        if self.connected:
            return True

        ok = self.mt5.initialize(path=self.mt5_path)
        if not ok:
            error = f"MT5 initialize failed: {self._last_error_text()}"
            self.state.set_connection(initialized=False, logged_in=False, account=None, terminal=None, last_error=error)
            _emit("runner.mt5.initialize_failed", {"error": error})
            return False

        login_ok = self.mt5.login(login=self.login, password=self.password, server=self.server)
        if not login_ok:
            error = f"MT5 login failed: {self._last_error_text()}"
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            self.state.set_connection(initialized=True, logged_in=False, account=None, terminal=None, last_error=error)
            _emit(
                "runner.mt5.login_failed",
                {
                    "error": error,
                    "server": self.server,
                    "login": self.login,
                },
            )
            return False

        account_info = self.mt5.account_info()
        terminal_info = self.mt5.terminal_info()
        account_payload = self._to_info_dict(account_info, ["login", "server", "name", "balance", "equity", "leverage"])
        terminal_payload = self._to_info_dict(
            terminal_info,
            ["community_account", "trade_allowed", "tradeapi_disabled", "path", "company", "name"],
        )
        self.state.set_connection(
            initialized=True,
            logged_in=True,
            account=account_payload,
            terminal=terminal_payload,
            last_error=None,
        )
        _emit(
            "runner.mt5.connected",
            {
                "server": account_payload.get("server") or self.server,
                "login": account_payload.get("login") or self.login,
                "terminal_path": terminal_payload.get("path"),
                "algo_trade_allowed": terminal_payload.get("trade_allowed"),
                "tradeapi_disabled": terminal_payload.get("tradeapi_disabled"),
            },
        )

        self.connected = True
        for symbol in self.symbols:
            try:
                broker_symbol = self._resolve_broker_symbol(symbol)
                self.state.set_symbol_selected(symbol=symbol, selected=True, broker_symbol=broker_symbol)
            except Exception as exc:
                message = str(exc)
                self.state.set_symbol_selected(symbol=symbol, selected=False, broker_symbol=symbol)
                self.state.set_symbol_error(symbol=symbol, error=message)
                _emit("runner.mt5.symbol_select_failed", {"symbol": symbol, "error": message})
        return True

    def _resolve_broker_symbol(self, symbol: str) -> str:
        key = symbol.strip().upper()
        if key in self._broker_symbols:
            cached = self._broker_symbols[key]
            if self.mt5.symbol_select(cached, True):
                return cached

        tried: list[str] = []
        candidates: list[str] = []
        mapped = self.symbol_map.get(key)
        if mapped:
            candidates.append(mapped)
        candidates.extend([key, f"{key}m", f"{key}.m", f"{key}.", f"{key}_"])
        for candidate in candidates:
            if candidate in tried:
                continue
            tried.append(candidate)
            if self.mt5.symbol_select(candidate, True):
                self._broker_symbols[key] = candidate
                return candidate

        try:
            matches = self.mt5.symbols_get(f"{key}*") or []
        except Exception:
            matches = []
        for match in matches:
            name = str(getattr(match, "name", "")).strip()
            if not name or name in tried:
                continue
            tried.append(name)
            if self.mt5.symbol_select(name, True):
                self._broker_symbols[key] = name
                return name

        raise RuntimeError(f"symbol_select failed for {key}: {self._last_error_text()} tried={tried}")

    def _timeframe_value(self, tf: str) -> int:
        mapping = {
            "M1": self.mt5.TIMEFRAME_M1,
            "M5": self.mt5.TIMEFRAME_M5,
            "M15": self.mt5.TIMEFRAME_M15,
            "M30": self.mt5.TIMEFRAME_M30,
            "H1": self.mt5.TIMEFRAME_H1,
            "H4": self.mt5.TIMEFRAME_H4,
            "D1": self.mt5.TIMEFRAME_D1,
        }
        return mapping[tf]

    def fetch_closed_candle(self, *, symbol: str, timeframe: str) -> dict:
        if not self.connected:
            raise RuntimeError("MT5 not connected.")
        broker_symbol = self._resolve_broker_symbol(symbol)
        tf_value = self._timeframe_value(timeframe)
        rates = self.mt5.copy_rates_from_pos(broker_symbol, tf_value, 1, 1)
        if rates is None or len(rates) == 0:
            error = f"copy_rates_from_pos failed for {symbol}/{broker_symbol} {timeframe}: {self._last_error_text()}"
            self.state.set_symbol_error(symbol=symbol, error=error)
            _emit("runner.mt5.copy_rates_failed", {"symbol": symbol, "timeframe": timeframe, "error": error})
            raise RuntimeError(error)

        row = rates[0]
        candle_time_utc = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
        candle_time_london = candle_time_utc.astimezone(self.london_tz)
        candle_time_broker = candle_time_utc.astimezone(self.broker_tz)
        tick = self.mt5.symbol_info_tick(broker_symbol)
        tick_time_utc: datetime | None = None
        bid = float(row["close"])
        ask = float(row["close"])
        if tick is not None:
            tick_ts = getattr(tick, "time", None)
            if tick_ts is not None:
                tick_time_utc = datetime.fromtimestamp(int(tick_ts), tz=timezone.utc)
            bid = float(getattr(tick, "bid", bid) or bid)
            ask = float(getattr(tick, "ask", ask) or ask)

        self.state.set_symbol_selected(symbol=symbol, selected=True, broker_symbol=broker_symbol)
        self.state.set_symbol_tick(symbol=symbol, tick_utc=tick_time_utc)
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle_time_utc": candle_time_utc.isoformat(),
            "candle_time_london": candle_time_london.isoformat(),
            "candle_time_broker": candle_time_broker.isoformat(),
            "broker_utc_offset_minutes": int(self.broker_offset_minutes),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "tick_volume": float(row["tick_volume"]),
            "bid": bid,
            "ask": ask,
            "source": "mt5_runner",
        }


def _headers(runner_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Runner-Key": runner_key,
    }


def _post(session: requests.Session, *, api_base: str, runner_key: str, path: str, payload: dict, timeout: int) -> requests.Response:
    return session.post(
        f"{api_base}{path}",
        headers=_headers(runner_key),
        json=payload,
        timeout=timeout,
    )


def _send_heartbeat(
    session: requests.Session,
    *,
    cfg: dict,
    state: RunnerState,
    symbols: list[str],
) -> None:
    snapshot = state.snapshot()
    symbols_ok = [symbol for symbol, row in snapshot.get("symbols", {}).items() if bool(row.get("selected"))]
    payload = {
        "runner_id": cfg["runner_id"],
        "version": cfg["runner_version"],
        "symbols_enabled": symbols,
        "symbols_ok": symbols_ok,
        "mt5_connected": bool(snapshot.get("mt5_initialized") and snapshot.get("mt5_logged_in")),
        "last_tick_utc": snapshot.get("last_tick_utc"),
        "last_ingest_utc": snapshot.get("last_ingest_utc"),
        "last_signal_utc": snapshot.get("last_signal_utc"),
        "last_telegram_sent_utc": snapshot.get("last_telegram_sent_utc"),
        "last_error": snapshot.get("last_error"),
    }
    response = _post(
        session,
        api_base=cfg["api_base"],
        runner_key=cfg["runner_key"],
        path="/api/runner/heartbeat",
        payload=payload,
        timeout=cfg["request_timeout_seconds"],
    )
    if response.status_code == 404:
        response = _post(
            session,
            api_base=cfg["api_base"],
            runner_key=cfg["runner_key"],
            path="/runner/mt5/heartbeat",
            payload=payload,
            timeout=cfg["request_timeout_seconds"],
        )
    if not response.ok:
        raise RuntimeError(f"heartbeat failed {response.status_code}: {response.text}")


def run_forever() -> None:
    cfg = _validate_env()
    symbols = _parse_symbols()
    timeframes = _parse_timeframes()
    symbol_map = _parse_symbol_map(os.getenv("RUNNER_SYMBOL_MAP_JSON"))
    state = RunnerState(symbols=symbols)
    mt5 = MT5Manager(
        state=state,
        symbols=symbols,
        mt5_path=cfg["mt5_path"],
        login=int(cfg["mt5_login"]),
        password=cfg["mt5_password"],
        server=cfg["mt5_server"],
        symbol_map=symbol_map,
        broker_offset_minutes=int(cfg["broker_utc_offset_minutes"]),
        london_tz=cfg["london_tz"],
    )
    session = requests.Session()

    control_server: ThreadingHTTPServer | None = None
    if cfg["control_enabled"]:
        control_server = _start_control_server(
            state=state,
            runner_key=cfg["runner_key"],
            host=cfg["control_host"],
            port=cfg["control_port"],
            require_key=bool(cfg["control_require_key"]),
        )

    _emit(
        "runner.started",
        {
            "runner_id": cfg["runner_id"],
            "api_base": cfg["api_base"],
            "symbols": symbols,
            "timeframes": timeframes,
            "loop_seconds": cfg["loop_seconds"],
            "heartbeat_seconds": cfg["heartbeat_seconds"],
            "conn_check_seconds": cfg["conn_check_seconds"],
            "runner_broker_utc_offset_minutes": int(cfg["broker_utc_offset_minutes"]),
            "runner_london_tz": cfg["london_tz_name"],
        },
    )

    heartbeat_due = datetime.now(timezone.utc)
    conn_check_due = datetime.now(timezone.utc)

    try:
        while True:
            started = datetime.now(timezone.utc)
            reconnect_reason = state.pop_reconnect()
            if reconnect_reason:
                _emit("runner.mt5.reconnect_requested", {"reason": reconnect_reason})
                conn_check_due = started

            if started >= conn_check_due:
                force = reconnect_reason is not None
                if not mt5.connect(force=force):
                    state.mark_error(state.snapshot().get("last_error") or "MT5 connection failed.")
                conn_check_due = started + timedelta(seconds=cfg["conn_check_seconds"])

            if not mt5.connected:
                # retry connection on next loop and still send heartbeat.
                conn_check_due = started
            else:
                for symbol in symbols:
                    for timeframe in timeframes:
                        try:
                            payload = mt5.fetch_closed_candle(symbol=symbol, timeframe=timeframe)
                            response = _post(
                                session,
                                api_base=cfg["api_base"],
                                runner_key=cfg["runner_key"],
                                path="/ingest/mt5/candle",
                                payload=payload,
                                timeout=cfg["request_timeout_seconds"],
                            )
                            if not response.ok:
                                message = (
                                    f"ingest failed symbol={symbol} timeframe={timeframe} "
                                    f"status={response.status_code} body={response.text}"
                                )
                                state.set_symbol_error(symbol=symbol, error=message)
                                state.mark_error(message)
                                _emit("runner.ingest.error", {"symbol": symbol, "timeframe": timeframe, "error": message})
                            else:
                                state.mark_ingest_success(
                                    candle_time_utc=_as_utc(datetime.fromisoformat(payload["candle_time_utc"]))
                                )
                        except Exception as exc:
                            message = str(exc)
                            state.set_symbol_error(symbol=symbol, error=message)
                            state.mark_error(message)
                            _emit("runner.ingest.exception", {"symbol": symbol, "timeframe": timeframe, "error": message})
                            mt5.connected = False
                            state.set_connection(
                                initialized=False,
                                logged_in=False,
                                account=None,
                                terminal=None,
                                last_error=message,
                            )
                            conn_check_due = datetime.now(timezone.utc)
                            break
                    if not mt5.connected:
                        break

            now_loop = datetime.now(timezone.utc)
            if now_loop >= heartbeat_due:
                try:
                    _send_heartbeat(session, cfg=cfg, state=state, symbols=symbols)
                    state.set_heartbeat_error(None)
                except Exception as exc:
                    message = str(exc)
                    state.set_heartbeat_error(message)
                    _emit("runner.heartbeat.error", {"error": message})
                heartbeat_due = now_loop + timedelta(seconds=cfg["heartbeat_seconds"])

            snapshot = state.snapshot()
            _emit(
                "runner.loop",
                {
                    "mt5_initialized": snapshot.get("mt5_initialized"),
                    "mt5_logged_in": snapshot.get("mt5_logged_in"),
                    "last_ingest_utc": snapshot.get("last_ingest_utc"),
                    "last_error": snapshot.get("last_error"),
                    "symbols_ok": [
                        sym for sym, row in snapshot.get("symbols", {}).items() if bool(row.get("selected"))
                    ],
                },
            )

            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            time.sleep(max(cfg["loop_seconds"] - elapsed, 1.0))
    finally:
        try:
            mt5.disconnect()
        except Exception:
            pass
        if control_server is not None:
            try:
                control_server.shutdown()
                control_server.server_close()
            except Exception:
                pass
        _emit("runner.stopped")


if __name__ == "__main__":
    run_forever()
