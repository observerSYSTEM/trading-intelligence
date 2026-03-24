from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv


def _load_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as exc:
        raise RuntimeError("MetaTrader5 package is not installed. Run: pip install MetaTrader5") from exc
    return mt5


def _resolve_timeframe(mt5, value: str):
    mapping = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }
    tf = (value or "").strip().upper()
    if tf not in mapping:
        raise RuntimeError(f"Unsupported timeframe '{value}'")
    return tf, mapping[tf]


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


def _resolve_london_tz(name: str | None):
    tz_name = (name or "Europe/London").strip() or "Europe/London"
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return timezone.utc


def _fetch_last_closed_candle():
    mt5 = _load_mt5()

    terminal_path = os.getenv("MT5_TERMINAL_PATH") or None
    mt5_login = (os.getenv("MT5_LOGIN") or "").strip()
    mt5_password = os.getenv("MT5_PASSWORD") or ""
    mt5_server = os.getenv("MT5_SERVER") or ""
    symbol = os.getenv("ORACLE_SYMBOL", "XAUUSD")
    timeframe_raw = os.getenv("ORACLE_TIMEFRAME", "M1")
    timeframe_label, timeframe = _resolve_timeframe(mt5, timeframe_raw)
    broker_offset_minutes = _parse_broker_offset_minutes(os.getenv("RUNNER_BROKER_UTC_OFFSET"))
    london_tz = _resolve_london_tz(os.getenv("RUNNER_LONDON_TZ"))

    if terminal_path:
        ok = mt5.initialize(path=terminal_path)
    else:
        ok = mt5.initialize()
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

    try:
        if mt5_login:
            login_ok = mt5.login(login=int(mt5_login), password=mt5_password, server=mt5_server)
            if not login_ok:
                raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(f"MT5 symbol_select failed for '{symbol}': {mt5.last_error()}")

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 1, 1)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No MT5 candle returned for {symbol} {timeframe_label}")

        row = rates[0]
        candle_time_utc = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
        candle_time_london = candle_time_utc.astimezone(london_tz)
        broker_tz = timezone(timedelta(minutes=int(broker_offset_minutes)))
        candle_time_broker = candle_time_utc.astimezone(broker_tz)

        return {
            "symbol": symbol,
            "timeframe": timeframe_label,
            "candle_time_utc": candle_time_utc.isoformat(),
            "candle_time_london": candle_time_london.isoformat(),
            "candle_time_broker": candle_time_broker.isoformat(),
            "broker_utc_offset_minutes": int(broker_offset_minutes),
            "o": float(row["open"]),
            "h": float(row["high"]),
            "l": float(row["low"]),
            "c": float(row["close"]),
            "tick_volume": float(row["tick_volume"]),
            "source": "mt5_runner",
        }
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def main() -> int:
    load_dotenv()

    app_url = (os.getenv("APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    runner_key = os.getenv("RUNNER_API_KEY") or ""
    if not runner_key:
        print("ERROR: RUNNER_API_KEY is not set.", file=sys.stderr)
        return 2

    payload = _fetch_last_closed_candle()
    endpoint = f"{app_url}/ingest/mt5/candle"

    response = requests.post(
        endpoint,
        json=payload,
        headers={"X-Runner-Key": runner_key},
        timeout=60,
    )
    if not response.ok:
        print(f"HTTP {response.status_code}: {response.text}", file=sys.stderr)
        return 3

    print(json.dumps(response.json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
