from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import default_configured_symbol, enabled_symbols_from_settings
from app.db.models import MT5Candle, MT5IngestStatus, RunnerStatus, User
from app.db.session import get_db
from app.services.data_provider import api_candle_mode, candle_provider_debug_labels, get_data_provider
from app.services.runner_control import fetch_runner_health
from app.services.targets_refresh import market_health_rows

router = APIRouter(tags=["health"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/health/ingest")
def ingest_health(
    symbol: str | None = None,
    timeframe: str = settings.ORACLE_TIMEFRAME,
    stale_after_seconds: int = 180,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("health_ingest", (RateLimitRule(limit=60, window_seconds=60),)),
):
    symbol_value = (symbol or default_configured_symbol()).strip().upper()
    row = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol_value, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )

    if api_candle_mode():
        try:
            provider = get_data_provider()
            candle = provider.get_latest_closed_candle(symbol=symbol_value, timeframe=timeframe)
            candle_time_utc = _as_utc(candle.time_utc)
            now_utc = datetime.now(timezone.utc)
            age_seconds = max(int((now_utc - candle_time_utc).total_seconds()), 0)
            is_stale = age_seconds > stale_after_seconds
            return {
                "ok": not is_stale,
                "status": "stale" if is_stale else "fresh",
                "symbol": symbol_value,
                "timeframe": timeframe,
                "last_candle_time_utc": candle_time_utc.isoformat(),
                "age_seconds": age_seconds,
                "stale_after_seconds": stale_after_seconds,
                "api_mode": True,
                **candle_provider_debug_labels(
                    latest_candle_source=getattr(candle, "source", None),
                    last_candle_time=candle_time_utc.isoformat(),
                    anchor_candle_source=None,
                    anchor_candle_status=None,
                ),
            }
        except Exception as exc:
            return {
                "ok": False,
                "status": "provider_error",
                "symbol": symbol_value,
                "timeframe": timeframe,
                "last_candle_time_utc": None,
                "age_seconds": None,
                "stale_after_seconds": stale_after_seconds,
                "api_mode": True,
                "error": str(exc),
                **candle_provider_debug_labels(),
            }

    if not row:
        return {
            "ok": False,
            "status": "no_data",
            "symbol": symbol_value,
            "timeframe": timeframe,
            "last_candle_time_utc": None,
            "age_seconds": None,
            "stale_after_seconds": stale_after_seconds,
        }

    now_utc = datetime.now(timezone.utc)
    candle_time_utc = _as_utc(row.time_utc)
    age_seconds = max(int((now_utc - candle_time_utc).total_seconds()), 0)
    is_stale = age_seconds > stale_after_seconds

    return {
        "ok": not is_stale,
        "status": "stale" if is_stale else "fresh",
        "symbol": row.symbol,
        "timeframe": row.timeframe,
        "last_candle_time_utc": candle_time_utc.isoformat(),
        "age_seconds": age_seconds,
        "stale_after_seconds": stale_after_seconds,
    }


@router.get("/health")
def health():
    return {"ok": True, "env": settings.APP_ENV}


@router.get("/health/db")
def health_db(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("health_db", (RateLimitRule(limit=30, window_seconds=60),)),
):
    db.execute(select(1))
    return {"ok": True}


@router.get("/health/stripe")
def health_stripe(
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("health_stripe", (RateLimitRule(limit=30, window_seconds=60),)),
):
    return {
        "ok": True,
        "configured": bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET),
    }


@router.get("/health/market")
def health_market(
    symbol: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(get_current_user),
    _limit: None = rate_limit("health_market", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbols = [symbol.strip().upper()] if symbol else enabled_symbols_from_settings()
    items = market_health_rows(db, symbols=symbols)
    if symbol:
        return items[0]
    return {"ok": True, "items": items}


@router.get("/health/runner")
def health_runner(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("health_runner", (RateLimitRule(limit=120, window_seconds=60),)),
):
    now_utc = datetime.now(timezone.utc)
    if api_candle_mode():
        symbol = default_configured_symbol()
        stale_after_seconds = max(int(settings.RUNNER_HEARTBEAT_STALE_SECONDS or 180), 30)
        ingest_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol).first()
        latest_candle = (
            db.query(MT5Candle)
            .filter(MT5Candle.symbol == symbol)
            .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
            .first()
        )
        last_ingest = None
        if ingest_row is not None:
            last_ingest_value = getattr(ingest_row, "updated_at", None) or ingest_row.last_ingested_at
            last_ingest = _as_utc(last_ingest_value)
        latest_candle_time = _as_utc(latest_candle.time_utc) if latest_candle else None
        lag_seconds = max(int((now_utc - last_ingest).total_seconds()), 0) if last_ingest else None
        provider_connected = bool(last_ingest is not None and lag_seconds is not None and lag_seconds <= stale_after_seconds)
        return {
            "ok": provider_connected,
            "mode": "api",
            "api_mode": True,
            "provider_connected": provider_connected,
            "mt5_connected": False,
            "mt5_initialized": None,
            "mt5_logged_in": None,
            "last_tick_utc": None,
            "last_ingest_utc": last_ingest.isoformat() if last_ingest else None,
            "lag_seconds": lag_seconds,
            "symbols_ok": [symbol] if provider_connected else [],
            "symbols": {},
            "account": None,
            "terminal": None,
            "server_time_utc": now_utc.isoformat(),
            "last_error": None if provider_connected else "API candle ingest is stale or missing.",
            "runner_control_configured": False,
            "runner_control_ok": None,
            "runner_control_error": None,
            "runner_control_warning": None,
            "runner_control_url": None,
            "runner_ok": provider_connected,
            "items": [],
            **candle_provider_debug_labels(
                latest_candle_source=(settings.CANDLE_PROVIDER or "api").strip().lower(),
                last_candle_time=latest_candle_time.isoformat() if latest_candle_time else None,
                anchor_candle_source=None,
                anchor_candle_status=None,
            ),
        }

    rows = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).all()
    remote = fetch_runner_health()
    remote_data = remote.get("data") if isinstance(remote.get("data"), dict) else {}
    remote_configured = bool(remote.get("configured"))
    remote_ok = bool(remote.get("ok"))
    remote_warning = remote.get("warning")
    remote_error = None if remote_ok else remote.get("error")

    if not rows:
        payload = {
            "ok": False,
            "mt5_connected": False,
            "mt5_initialized": bool(remote_data.get("mt5_initialized")),
            "mt5_logged_in": bool(remote_data.get("mt5_logged_in")),
            "last_tick_utc": None,
            "last_ingest_utc": None,
            "lag_seconds": None,
            "symbols_ok": [],
            "symbols": remote_data.get("symbols", {}) if isinstance(remote_data.get("symbols"), dict) else {},
            "account": remote_data.get("account") if isinstance(remote_data.get("account"), dict) else None,
            "terminal": remote_data.get("terminal") if isinstance(remote_data.get("terminal"), dict) else None,
            "server_time_utc": remote_data.get("server_time_utc"),
            "last_error": remote_data.get("last_error") if remote_data else None,
            "runner_control_configured": remote_configured,
            "runner_control_ok": remote_ok,
            "runner_control_error": remote_error,
            "runner_control_warning": remote_warning,
            "runner_ok": remote_data.get("runner_ok") if isinstance(remote_data, dict) else None,
            "items": [],
            "reason": "no_runner_status",
        }
        if remote_ok and isinstance(remote_data, dict):
            payload["ok"] = bool(remote_data.get("runner_ok", True))
            payload["mt5_connected"] = bool(
                remote_data.get("mt5_logged_in", False) and remote_data.get("mt5_initialized", False)
            )
            payload["last_tick_utc"] = remote_data.get("last_tick_utc")
            payload["last_ingest_utc"] = remote_data.get("last_ingest_utc")
        return payload

    items: list[dict] = []
    for row in rows:
        last_tick = _as_utc(row.last_tick_utc) if row.last_tick_utc else None
        last_ingest = _as_utc(row.last_ingest_utc) if row.last_ingest_utc else None
        last_seen = _as_utc(row.updated_at)
        reference = last_ingest or last_tick or last_seen
        lag_seconds = max(int((now_utc - reference).total_seconds()), 0)
        items.append(
            {
                "runner_id": row.runner_id,
                "mt5_connected": bool(row.mt5_connected),
                "last_tick_utc": last_tick.isoformat() if last_tick else None,
                "last_ingest_utc": last_ingest.isoformat() if last_ingest else None,
                "last_seen_utc": last_seen.isoformat(),
                "last_ok_at_utc": _as_utc(row.last_ok_at).isoformat() if row.last_ok_at else None,
                "last_error": row.last_error,
                "lag_seconds": lag_seconds,
                "symbols_ok": row.symbols_ok_json if isinstance(row.symbols_ok_json, list) else [],
            }
        )

    latest = items[0]
    remote_symbols = remote_data.get("symbols", {}) if isinstance(remote_data.get("symbols"), dict) else {}
    merged_last_error = (
        (remote_data.get("last_error") if isinstance(remote_data, dict) else None)
        or latest.get("last_error")
    )
    mt5_initialized = bool(remote_data.get("mt5_initialized")) if (remote_ok and isinstance(remote_data, dict)) else None
    mt5_logged_in = bool(remote_data.get("mt5_logged_in")) if (remote_ok and isinstance(remote_data, dict)) else None
    mt5_connected = (
        bool(mt5_initialized and mt5_logged_in)
        if (mt5_initialized is not None and mt5_logged_in is not None and remote_ok)
        else bool(latest["mt5_connected"])
    )
    last_tick_utc = (
        remote_data.get("last_tick_utc")
        if isinstance(remote_data, dict) and remote_data.get("last_tick_utc")
        else latest["last_tick_utc"]
    )
    last_ingest_utc = (
        remote_data.get("last_ingest_utc")
        if isinstance(remote_data, dict) and remote_data.get("last_ingest_utc")
        else latest["last_ingest_utc"]
    )
    symbols_ok = (
        [symbol for symbol, data in remote_symbols.items() if isinstance(data, dict) and bool(data.get("selected"))]
        if remote_symbols
        else latest["symbols_ok"]
    )
    return {
        "ok": True if rows else False,
        "mt5_connected": mt5_connected,
        "mt5_initialized": mt5_initialized,
        "mt5_logged_in": mt5_logged_in,
        "last_tick_utc": last_tick_utc,
        "last_ingest_utc": last_ingest_utc,
        "lag_seconds": latest["lag_seconds"],
        "symbols_ok": symbols_ok,
        "symbols": remote_symbols,
        "account": remote_data.get("account") if isinstance(remote_data.get("account"), dict) else None,
        "terminal": remote_data.get("terminal") if isinstance(remote_data.get("terminal"), dict) else None,
        "server_time_utc": remote_data.get("server_time_utc") if isinstance(remote_data, dict) else None,
        "last_error": merged_last_error,
        "runner_control_configured": remote_configured,
        "runner_control_ok": remote_ok,
        "runner_control_error": remote_error,
        "runner_control_warning": remote_warning,
        "runner_control_url": remote.get("url"),
        "runner_ok": remote_data.get("runner_ok") if isinstance(remote_data, dict) else None,
        "items": items,
    }
