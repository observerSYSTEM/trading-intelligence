from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.symbols import enabled_symbols_from_settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.db.session import get_db
from app.services.audit import log_audit
from app.services.admin_oracle_automation import run_oracle_and_send
from app.services.autotrade_service import (
    queue_autotrade_jobs_for_symbol,
    set_global_autotrade_enabled,
    set_symbol_autotrade_enabled,
    set_user_autotrade_enabled,
)
from app.services.oracle_scheduler import (
    recompute_permission_today,
    recompute_quarterly_snapshots,
    run_confirm_now,
    run_oracle_for_symbols,
)
from app.services.oracle_exec import build_execution_instruction

router = APIRouter(prefix="/admin/oracle", tags=["admin-oracle"])


class RunNowIn(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=32)
    symbols: list[str] = Field(default_factory=list)


class RecomputeIn(BaseModel):
    symbol: str | None = Field(default=None, min_length=1, max_length=32)


class RunAndSendIn(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["XAUUSD"], min_length=1)
    tier_min: Literal["basic", "pro", "elite"] = "basic"
    mode: Literal["daily_bias", "intraday_update"] = "daily_bias"
    dry_run: bool = False


class ExecIn(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    target_tier: Literal["elite"] = "elite"
    session: Literal["auto", "any", "london", "newyork", "ny"] = "auto"
    ttl_seconds: int | None = Field(default=None, ge=60, le=7200)


class ExecQueueIn(BaseModel):
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    strategy_name: Literal["DAILY_BIAS", "LIQ_SWEEP", "NEWS_EXEC", "VOL_MANIP", "ZONE_TO_ZONE"] = "DAILY_BIAS"
    volume: float | None = Field(default=None, gt=0)
    mode: Literal["daily_bias", "intraday_update"] = "daily_bias"
    user_id: UUID | None = None


class AutoTradeGlobalIn(BaseModel):
    autotrade_enabled: bool


class AutoTradeUserIn(BaseModel):
    user_id: UUID
    autotrade_enabled: bool


class AutoTradeSymbolIn(BaseModel):
    user_id: UUID
    symbol: str = Field(..., min_length=1, max_length=32)
    autotrade_enabled: bool


def _resolve_run_symbols(payload: RunNowIn) -> list[str]:
    symbols: list[str] = []
    for value in payload.symbols:
        symbol = value.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if payload.symbol:
        symbol = payload.symbol.strip().upper()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if symbols:
        return symbols
    return enabled_symbols_from_settings()


@router.post("/run-now")
def admin_oracle_run_now(
    payload: RunNowIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_run_now", (RateLimitRule(limit=30, window_seconds=60),)),
):
    symbols = _resolve_run_symbols(payload)
    log_audit(db, action="admin.oracle.run_now", user_id=_admin.id, request=request, meta={"symbols": symbols})
    db.commit()
    result = run_oracle_for_symbols(symbols)
    return result


@router.post("/run")
def admin_oracle_run_compat(
    payload: RunNowIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_run", (RateLimitRule(limit=30, window_seconds=60),)),
):
    symbols = _resolve_run_symbols(payload)
    log_audit(db, action="admin.oracle.run", user_id=_admin.id, request=request, meta={"symbols": symbols})
    db.commit()
    result = run_oracle_for_symbols(symbols)
    return result


@router.post("/run-and-send")
def admin_oracle_run_and_send(
    payload: RunAndSendIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_run_and_send", (RateLimitRule(limit=20, window_seconds=60),)),
):
    result = run_oracle_and_send(
        db,
        symbols=payload.symbols,
        tier_min=payload.tier_min,
        mode=payload.mode,
        dry_run=payload.dry_run,
        admin_user_id=_admin.id,
    )
    log_audit(
        db,
        action="admin.oracle.run_and_send",
        user_id=_admin.id,
        request=request,
        meta={
            "symbols": payload.symbols,
            "tier_min": payload.tier_min,
            "mode": payload.mode,
            "dry_run": payload.dry_run,
            "sent_count": result.get("sent_count"),
            "skipped_count": result.get("skipped_count"),
            "blocked_count": result.get("blocked_count"),
        },
    )
    db.commit()
    return result


@router.post("/confirm/{run_id}")
def admin_oracle_confirm_now(
    run_id: str,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_confirm", (RateLimitRule(limit=60, window_seconds=60),)),
):
    log_audit(db, action="admin.oracle.confirm", user_id=_admin.id, request=request, meta={"run_id": run_id})
    db.commit()
    return run_confirm_now(run_id=run_id)


@router.post("/quarterly/recompute")
def admin_oracle_quarterly_recompute(
    payload: RecomputeIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_quarterly_recompute", (RateLimitRule(limit=20, window_seconds=60),)),
):
    log_audit(
        db,
        action="admin.oracle.quarterly.recompute",
        user_id=_admin.id,
        request=request,
        meta={"symbol": payload.symbol},
    )
    db.commit()
    symbol = payload.symbol.strip().upper() if payload.symbol else None
    return recompute_quarterly_snapshots(symbol=symbol)


@router.post("/permission/recompute")
def admin_oracle_permission_recompute(
    payload: RecomputeIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_permission_recompute", (RateLimitRule(limit=20, window_seconds=60),)),
):
    log_audit(
        db,
        action="admin.oracle.permission.recompute",
        user_id=_admin.id,
        request=request,
        meta={"symbol": payload.symbol},
    )
    db.commit()
    symbol = payload.symbol.strip().upper() if payload.symbol else None
    return recompute_permission_today(symbol=symbol)


@router.post("/exec")
def admin_oracle_exec(
    payload: ExecIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_exec", (RateLimitRule(limit=30, window_seconds=60),)),
):
    result = build_execution_instruction(
        db,
        symbol=payload.symbol.strip().upper(),
        target_tier=payload.target_tier,
        requested_session=payload.session,
        ttl_seconds=payload.ttl_seconds,
    )
    meta = {
        "symbol": payload.symbol.strip().upper(),
        "enabled": bool(result.get("enabled")),
        "side": result.get("side"),
        "requested_session": payload.session,
        "target_tier": payload.target_tier,
        "reasons": (result.get("meta") or {}).get("reasons", []),
    }
    log_audit(
        db,
        action="admin.oracle.exec",
        user_id=_admin.id,
        request=request,
        meta=meta,
    )
    db.commit()
    return result


@router.post("/exec/queue")
def admin_oracle_exec_queue(
    payload: ExecQueueIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_exec_queue", (RateLimitRule(limit=30, window_seconds=60),)),
):
    result = queue_autotrade_jobs_for_symbol(
        db,
        symbol=payload.symbol.strip().upper(),
        strategy_name=payload.strategy_name,
        volume=payload.volume,
        user_id=payload.user_id,
        mode=payload.mode,
    )
    log_audit(
        db,
        action="admin.oracle.exec.queue",
        user_id=_admin.id,
        request=request,
        meta={
            "symbol": payload.symbol,
            "strategy_name": payload.strategy_name,
            "mode": payload.mode,
            "user_id": str(payload.user_id) if payload.user_id else None,
            "created_count": result.get("created_count"),
            "blocked_count": result.get("blocked_count"),
        },
    )
    db.commit()
    return result


@router.post("/autotrade/global")
def admin_set_autotrade_global(
    payload: AutoTradeGlobalIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_autotrade_global", (RateLimitRule(limit=30, window_seconds=60),)),
):
    result = set_global_autotrade_enabled(db, enabled=payload.autotrade_enabled)
    log_audit(
        db,
        action="admin.oracle.autotrade.global",
        user_id=_admin.id,
        request=request,
        meta={"autotrade_enabled": payload.autotrade_enabled},
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/autotrade/user")
def admin_set_autotrade_user(
    payload: AutoTradeUserIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_autotrade_user", (RateLimitRule(limit=60, window_seconds=60),)),
):
    result = set_user_autotrade_enabled(db, user_id=payload.user_id, enabled=payload.autotrade_enabled)
    log_audit(
        db,
        action="admin.oracle.autotrade.user",
        user_id=_admin.id,
        request=request,
        meta={"target_user_id": str(payload.user_id), "autotrade_enabled": payload.autotrade_enabled},
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/autotrade/symbol")
def admin_set_autotrade_symbol(
    payload: AutoTradeSymbolIn,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_oracle_autotrade_symbol", (RateLimitRule(limit=120, window_seconds=60),)),
):
    result = set_symbol_autotrade_enabled(
        db,
        user_id=payload.user_id,
        symbol=payload.symbol.strip().upper(),
        enabled=payload.autotrade_enabled,
    )
    log_audit(
        db,
        action="admin.oracle.autotrade.symbol",
        user_id=_admin.id,
        request=request,
        meta={
            "target_user_id": str(payload.user_id),
            "symbol": payload.symbol.strip().upper(),
            "autotrade_enabled": payload.autotrade_enabled,
        },
    )
    db.commit()
    return {"ok": True, **result}
