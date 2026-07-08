from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SUPPORTED_SYMBOLS = ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"]


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _env_provider(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'").lower()


def _provider_mode() -> str:
    data_provider = _env_provider(os.getenv("DATA_PROVIDER"))
    market_data_provider = _env_provider(os.getenv("MARKET_DATA_PROVIDER"))
    if _env_bool(os.getenv("DISABLE_MT5")):
        return "api"
    if data_provider == "api" or market_data_provider == "api":
        return "api"

    provider = data_provider or market_data_provider or "mt5"
    if provider in {"twelve_data", "twelve-data", "twelve"}:
        return "twelvedata"
    return provider


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
        return ZoneInfo(tz_name), tz_name
    except ZoneInfoNotFoundError:
        return timezone.utc, "UTC"


def _load_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except Exception as exc:
        raise RuntimeError("MetaTrader5 package is not installed. Run: pip install MetaTrader5") from exc
    return mt5


def _parse_csv(raw: str | None, fallback: list[str]) -> list[str]:
    if not raw:
        return list(fallback)
    values: list[str] = []
    for item in raw.split(","):
        value = item.strip().upper()
        if value and value not in values:
            values.append(value)
    return values or list(fallback)


def _payload_item_count(payload, key: str | None = None) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and key:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def _load_finnhub_provider():
    try:
        from runner.providers.finnhub_provider import FinnhubProvider
    except ModuleNotFoundError:
        from providers.finnhub_provider import FinnhubProvider  # type: ignore
    return FinnhubProvider


def _load_api_candle_provider():
    from runner.providers.candle_provider_factory import get_candle_provider_from_env

    return get_candle_provider_from_env()


def _runner_symbols() -> list[str]:
    raw_runner_symbols = (os.getenv("RUNNER_SYMBOLS") or "").strip()
    raw_oracle_symbols = (os.getenv("ORACLE_ENABLED_SYMBOLS") or "").strip()
    if raw_runner_symbols:
        symbols = _parse_csv(raw_runner_symbols, fallback=SUPPORTED_SYMBOLS)
    elif raw_oracle_symbols:
        symbols = _parse_csv(raw_oracle_symbols, fallback=SUPPORTED_SYMBOLS)
    else:
        symbols = list(SUPPORTED_SYMBOLS)
    return [s for s in symbols if s in SUPPORTED_SYMBOLS] or list(SUPPORTED_SYMBOLS)


def _run_api_candle_ingest_loop(*, app_url: str, runner_key: str, interval: int, run_once: bool) -> int:
    if not runner_key:
        print("ERROR: RUNNER_API_KEY is not set.", file=sys.stderr)
        return 2
    if interval < 5:
        print("ERROR: RUNNER_INTERVAL_SECONDS must be >= 5.", file=sys.stderr)
        return 3

    provider = _load_api_candle_provider()
    endpoint = f"{app_url}/ingest/mt5/candle"
    symbols = _runner_symbols()
    timeframes = _parse_csv(os.getenv("RUNNER_TIMEFRAMES"), fallback=["M1", "M15", "H1"])
    london_tz, london_tz_name = _resolve_london_tz(os.getenv("RUNNER_LONDON_TZ"))

    print(
        "API candle ingest runner started. "
        f"endpoint={endpoint} interval={interval}s once={run_once} symbols={symbols} timeframes={timeframes} "
        f"london_tz={london_tz_name} skipped_mt5=true",
        flush=True,
    )

    try:
        while True:
            loop_started = datetime.now(timezone.utc).isoformat()
            loop_ok = True
            posted = 0
            failures: list[dict] = []
            for symbol in symbols:
                for timeframe in timeframes:
                    try:
                        candle = provider.get_latest_closed_candle(symbol=symbol, timeframe=timeframe)
                        candle_time_utc = candle.time_utc.astimezone(timezone.utc)
                        payload = {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "candle_time_utc": candle_time_utc.isoformat(),
                            "candle_time_london": candle_time_utc.astimezone(london_tz).isoformat(),
                            "candle_time_broker": candle_time_utc.isoformat(),
                            "broker_utc_offset_minutes": 0,
                            "o": float(candle.open),
                            "h": float(candle.high),
                            "l": float(candle.low),
                            "c": float(candle.close),
                            "tick_volume": candle.volume,
                            "bid": float(candle.close),
                            "ask": float(candle.close),
                            "source": candle.source or "api_candle_runner_loop",
                        }
                        response = requests.post(
                            endpoint,
                            json=payload,
                            headers={"X-Runner-Key": runner_key},
                            timeout=60,
                        )
                        if response.ok:
                            posted += 1
                            print(
                                json.dumps(
                                    {
                                        "event": "api_candle_ingest_write_ok",
                                        "symbol": symbol,
                                        "timeframe": timeframe,
                                        "candle_time_utc": candle_time_utc.isoformat(),
                                        "source": candle.source or "api_candle_runner_loop",
                                    }
                                ),
                                flush=True,
                            )
                        else:
                            loop_ok = False
                            print(
                                json.dumps(
                                    {
                                        "event": "api_candle_ingest_write_failed",
                                        "symbol": symbol,
                                        "timeframe": timeframe,
                                        "candle_time_utc": candle_time_utc.isoformat(),
                                        "http_status": response.status_code,
                                        "body": response.text,
                                    }
                                ),
                                flush=True,
                            )
                            failures.append(
                                {
                                    "symbol": symbol,
                                    "timeframe": timeframe,
                                    "http_status": response.status_code,
                                    "body": response.text,
                                }
                            )
                    except Exception as exc:
                        loop_ok = False
                        print(
                            json.dumps(
                                {
                                    "event": "api_candle_ingest_write_failed",
                                    "symbol": symbol,
                                    "timeframe": timeframe,
                                    "error": str(exc),
                                }
                            ),
                            flush=True,
                        )
                        failures.append({"symbol": symbol, "timeframe": timeframe, "error": str(exc)})

            print(
                json.dumps(
                    {
                        "at": loop_started,
                        "status": "ok" if not failures else "partial",
                        "provider": provider.name,
                        "skipped_mt5": True,
                        "posted": posted,
                        "total": len(symbols) * len(timeframes),
                        "failures": failures,
                    }
                ),
                flush=True,
            )

            if run_once:
                return 0 if loop_ok else 4

            time.sleep(interval)
    except KeyboardInterrupt:
        print("API candle runner stopped.", flush=True)
        return 0


def _run_finnhub_ingest_loop(*, interval: int, run_once: bool) -> int:
    FinnhubProvider = _load_finnhub_provider()
    provider = FinnhubProvider()

    print(
        "Finnhub market/news runner started. "
        f"interval={interval}s once={run_once} skipped_mt5=true",
        flush=True,
    )

    try:
        while True:
            loop_started = datetime.now(timezone.utc)
            loop_ok = True
            try:
                from_date = loop_started.date()
                to_date = from_date + timedelta(days=7)
                economic_calendar = provider.get_economic_calendar(from_date, to_date)
                market_news = provider.get_market_news(category="general")
                forex_news = provider.get_forex_news_or_general_news()
                print(
                    json.dumps(
                        {
                            "at": loop_started.isoformat(),
                            "status": "ok",
                            "provider": "finnhub",
                            "skipped_mt5": True,
                            "from_date": from_date.isoformat(),
                            "to_date": to_date.isoformat(),
                            "calendar_events": _payload_item_count(economic_calendar, "economicCalendar"),
                            "market_news_count": _payload_item_count(market_news),
                            "forex_news_count": _payload_item_count(forex_news),
                            "economic_calendar": economic_calendar,
                            "market_news": market_news,
                            "forex_news": forex_news,
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                loop_ok = False
                print(
                    json.dumps(
                        {
                            "at": loop_started.isoformat(),
                            "status": "error",
                            "provider": "finnhub",
                            "skipped_mt5": True,
                            "error": str(exc),
                        }
                    ),
                    flush=True,
                )

            if run_once:
                return 0 if loop_ok else 4

            time.sleep(interval)
    except KeyboardInterrupt:
        print("Finnhub market/news runner stopped.", flush=True)
        return 0


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
    for k, v in data.items():
        key = str(k).strip().upper()
        value = str(v).strip()
        if key and value:
            out[key] = value
    return out


def _resolve_broker_symbol(mt5, canonical_symbol: str, symbol_map: dict[str, str]) -> str:
    tried: list[str] = []
    candidates: list[str] = []
    mapped = symbol_map.get(canonical_symbol)
    if mapped:
        candidates.append(mapped)
    candidates.extend(
        [
            canonical_symbol,
            f"{canonical_symbol}m",
            f"{canonical_symbol}.m",
            f"{canonical_symbol}.",
        ]
    )

    for candidate in candidates:
        if candidate in tried:
            continue
        tried.append(candidate)
        if mt5.symbol_select(candidate, True):
            return candidate

    try:
        matches = mt5.symbols_get(f"{canonical_symbol}*") or []
    except Exception:
        matches = []
    for match in matches:
        name = str(getattr(match, "name", "")).strip()
        if not name or name in tried:
            continue
        tried.append(name)
        if mt5.symbol_select(name, True):
            return name

    raise RuntimeError(f"MT5 symbol_select failed for '{canonical_symbol}'. Tried: {tried}")


def _fetch_last_closed_candle(
    mt5,
    symbol: str,
    timeframe_label: str,
    symbol_map: dict[str, str],
    *,
    broker_offset_minutes: int,
    london_tz,
):
    label, timeframe = _resolve_timeframe(mt5, timeframe_label)
    broker_symbol = _resolve_broker_symbol(mt5, symbol, symbol_map)

    rates = mt5.copy_rates_from_pos(broker_symbol, timeframe, 1, 1)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No MT5 candle returned for {symbol} ({broker_symbol}) {label}")

    row = rates[0]
    candle_time_utc = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
    candle_time_london = candle_time_utc.astimezone(london_tz)
    broker_tz = timezone(timedelta(minutes=int(broker_offset_minutes)))
    candle_time_broker = candle_time_utc.astimezone(broker_tz)
    tick = mt5.symbol_info_tick(broker_symbol)
    bid = float(getattr(tick, "bid", row["close"])) if tick is not None else float(row["close"])
    ask = float(getattr(tick, "ask", row["close"])) if tick is not None else float(row["close"])
    return {
        "symbol": symbol,
        "timeframe": label,
        "candle_time_utc": candle_time_utc.isoformat(),
        "candle_time_london": candle_time_london.isoformat(),
        "candle_time_broker": candle_time_broker.isoformat(),
        "broker_utc_offset_minutes": int(broker_offset_minutes),
        "o": float(row["open"]),
        "h": float(row["high"]),
        "l": float(row["low"]),
        "c": float(row["close"]),
        "tick_volume": float(row["tick_volume"]),
        "bid": bid,
        "ask": ask,
        "source": "mt5_runner_loop",
        "broker_symbol": broker_symbol,
    }


def main() -> int:
    load_dotenv()

    app_url = (os.getenv("API_BASE") or os.getenv("APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    runner_key = os.getenv("RUNNER_API_KEY") or ""
    interval = int(os.getenv("RUNNER_INTERVAL_SECONDS") or "60")
    run_once = _env_bool(os.getenv("RUNNER_ONCE"))
    provider_mode = _provider_mode()
    print(
        json.dumps(
            {
                "event": "runner_provider_selected",
                "DATA_PROVIDER": os.getenv("DATA_PROVIDER"),
                "MARKET_DATA_PROVIDER": os.getenv("MARKET_DATA_PROVIDER"),
                "DISABLE_MT5": os.getenv("DISABLE_MT5"),
                "resolved_provider": provider_mode,
            }
        ),
        flush=True,
    )
    if provider_mode in {"api", "twelvedata"}:
        if provider_mode == "twelvedata" and not os.getenv("CANDLE_PROVIDER"):
            os.environ["CANDLE_PROVIDER"] = "twelvedata"
            os.environ.setdefault("CANDLE_FALLBACK_PROVIDER", "")
        return _run_api_candle_ingest_loop(
            app_url=app_url,
            runner_key=runner_key,
            interval=interval,
            run_once=run_once,
        )
    if provider_mode == "finnhub":
        return _run_finnhub_ingest_loop(interval=interval, run_once=run_once)

    endpoint = f"{app_url}/ingest/mt5/candle"
    symbol_map = _parse_symbol_map(os.getenv("RUNNER_SYMBOL_MAP_JSON"))
    broker_offset_minutes = _parse_broker_offset_minutes(os.getenv("RUNNER_BROKER_UTC_OFFSET"))
    london_tz, london_tz_name = _resolve_london_tz(os.getenv("RUNNER_LONDON_TZ"))

    symbols = _runner_symbols()
    timeframes = _parse_csv(os.getenv("RUNNER_TIMEFRAMES"), fallback=["M1", "M15", "H1"])

    if not runner_key:
        print("ERROR: RUNNER_API_KEY is not set.", file=sys.stderr)
        return 2
    if interval < 5:
        print("ERROR: RUNNER_INTERVAL_SECONDS must be >= 5.", file=sys.stderr)
        return 3

    mt5 = _load_mt5()
    terminal_path = os.getenv("MT5_TERMINAL_PATH") or None
    mt5_login = (os.getenv("MT5_LOGIN") or "").strip()
    mt5_password = os.getenv("MT5_PASSWORD") or ""
    mt5_server = os.getenv("MT5_SERVER") or ""

    print(
        "MT5 ingest runner started. "
        f"endpoint={endpoint} interval={interval}s once={run_once} symbols={symbols} timeframes={timeframes} "
        f"broker_offset_minutes={broker_offset_minutes} london_tz={london_tz_name}",
        flush=True,
    )

    try:
        while True:
            loop_started = datetime.now(timezone.utc).isoformat()
            loop_ok = True
            try:
                if terminal_path:
                    ok = mt5.initialize(path=terminal_path)
                else:
                    ok = mt5.initialize()
                if not ok:
                    raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

                if mt5_login:
                    login_ok = mt5.login(login=int(mt5_login), password=mt5_password, server=mt5_server)
                    if not login_ok:
                        raise RuntimeError(f"MT5 login failed: {mt5.last_error()}")

                posted = 0
                failures: list[dict] = []
                for symbol in symbols:
                    for timeframe in timeframes:
                        try:
                            payload = _fetch_last_closed_candle(
                                mt5,
                                symbol,
                                timeframe,
                                symbol_map,
                                broker_offset_minutes=broker_offset_minutes,
                                london_tz=london_tz,
                            )
                            response = requests.post(
                                endpoint,
                                json=payload,
                                headers={"X-Runner-Key": runner_key},
                                timeout=60,
                            )
                            if response.ok:
                                posted += 1
                            else:
                                loop_ok = False
                                failures.append(
                                    {
                                        "symbol": symbol,
                                        "timeframe": timeframe,
                                        "http_status": response.status_code,
                                        "body": response.text,
                                    }
                                )
                        except Exception as exc:
                            loop_ok = False
                            failures.append({"symbol": symbol, "timeframe": timeframe, "error": str(exc)})

                print(
                    json.dumps(
                        {
                            "at": loop_started,
                            "status": "ok" if not failures else "partial",
                            "posted": posted,
                            "total": len(symbols) * len(timeframes),
                            "failures": failures,
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                loop_ok = False
                print(
                    json.dumps(
                        {
                            "at": loop_started,
                            "status": "error",
                            "error": str(exc),
                        }
                    ),
                    flush=True,
                )
            finally:
                try:
                    mt5.shutdown()
                except Exception:
                    pass

            if run_once:
                return 0 if loop_ok else 4

            time.sleep(interval)
    except KeyboardInterrupt:
        print("MT5 ingest runner stopped.", flush=True)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
