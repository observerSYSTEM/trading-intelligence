from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _env_bool(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _status(ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"ok": bool(ok), "detail": detail}


def _check_architecture() -> dict[str, Any]:
    machine = platform.machine().lower()
    return _status(machine in {"aarch64", "arm64", "x86_64", "amd64"}, {"machine": machine})


def _check_mt5_not_required() -> dict[str, Any]:
    api_mode = (
        _env_bool(os.getenv("DISABLE_MT5"))
        or (os.getenv("DATA_PROVIDER") or "").strip().lower() == "api"
        or (os.getenv("MARKET_DATA_PROVIDER") or "").strip().lower() == "api"
    )
    mt5_installed = importlib.util.find_spec("MetaTrader5") is not None
    return _status(api_mode, {"api_mode": api_mode, "metatrader5_installed": mt5_installed})


def _check_env() -> dict[str, Any]:
    required = [
        "DATA_PROVIDER",
        "MARKET_DATA_PROVIDER",
        "DISABLE_MT5",
        "CANDLE_PROVIDER",
        "CANDLE_FALLBACK_PROVIDER",
        "NEWS_PROVIDER",
        "OANDA_API_KEY",
        "OANDA_ACCOUNT_ID",
        "TWELVE_DATA_API_KEY",
        "FINNHUB_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "JWT_SECRET",
        "RUNNER_API_KEY",
        "NEXT_PUBLIC_API_BASE_URL",
    ]
    missing = [key for key in required if not (os.getenv(key) or "").strip()]
    return _status(not missing, {"missing": missing})


def _check_database() -> dict[str, Any]:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return _status(False, "DATABASE_URL is not set")
    try:
        from sqlalchemy import create_engine, text

        from app.core.db_url import normalize_database_url

        engine = create_engine(normalize_database_url(database_url), pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
        engine.dispose()
        return _status(True)
    except Exception as exc:
        return _status(False, str(exc))


def _check_redis() -> dict[str, Any]:
    redis_url = (os.getenv("REDIS_URL") or "").strip()
    if not redis_url:
        return _status(False, "REDIS_URL is not set")
    try:
        import redis

        client = redis.Redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
        return _status(bool(client.ping()))
    except Exception as exc:
        return _status(False, str(exc))


def _check_oanda() -> dict[str, Any]:
    if not (os.getenv("OANDA_API_KEY") and os.getenv("OANDA_ACCOUNT_ID")):
        return _status(False, "OANDA_API_KEY and OANDA_ACCOUNT_ID are required")
    try:
        from runner.providers.oanda_candle_provider import OandaCandleProvider

        candle = OandaCandleProvider().get_latest_closed_candle("XAUUSD", "M15")
        return _status(
            True,
            {
                "symbol": candle.symbol,
                "timeframe": candle.timeframe,
                "time": candle.time_utc.isoformat(),
                "source": candle.source,
                "complete": candle.complete,
            },
        )
    except Exception as exc:
        return _status(False, str(exc))


def _check_routes() -> dict[str, Any]:
    try:
        from app.main import app

        paths = {getattr(route, "path", "") for route in app.routes}
        expected = {
            "/health",
            "/lce/checkpoint/{symbol}",
            "/observer/decision/{symbol}",
            "/observer/recommendation/{symbol}",
        }
        missing = sorted(expected - paths)
        return _status(not missing, {"missing": missing})
    except Exception as exc:
        return _status(False, str(exc))


def _check_frontend_base() -> dict[str, Any]:
    value = (os.getenv("NEXT_PUBLIC_API_BASE_URL") or "").strip()
    ok = bool(value and not value.endswith("/"))
    return _status(ok, value or "NEXT_PUBLIC_API_BASE_URL is not set")


def main() -> int:
    load_dotenv(ROOT / ".env")
    checks = {
        "python_architecture": _check_architecture(),
        "no_metatrader5_import_requirement": _check_mt5_not_required(),
        "environment_completeness": _check_env(),
        "database_connectivity": _check_database(),
        "redis_connectivity": _check_redis(),
        "oanda_candle_fetch": _check_oanda(),
        "fastapi_route_registration": _check_routes(),
        "lce_route_registered": _check_routes(),
        "ode_route_registered": _check_routes(),
        "ore_route_registered": _check_routes(),
        "frontend_api_base": _check_frontend_base(),
    }
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if all(item["ok"] for item in checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
