from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_runner_auth
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import LiquiditySignal, RunnerHeartbeat, RunnerStatus, SignalDelivery, User
from app.db.session import get_db
from app.services.autotrade_service import next_trade_job_for_runner, submit_trade_job_result, sync_positions

router = APIRouter(prefix="/runner", tags=["runner"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RunnerHeartbeatIn(BaseModel):
    runner_id: str = Field(..., min_length=1, max_length=64)
    version: str = Field(default="unknown", min_length=1, max_length=64)
    symbols_enabled: list[str] = Field(default_factory=list)
    mt5_connected: bool | None = None
    last_tick_utc: datetime | None = None
    last_ingest_utc: datetime | None = None
    last_signal_utc: datetime | None = None
    last_telegram_sent_utc: datetime | None = None
    symbols_ok: list[str] = Field(default_factory=list)
    last_error: str | None = Field(default=None, max_length=1024)


class RunnerJobResultIn(BaseModel):
    status: Literal["filled", "failed", "canceled"]
    broker_ticket: str | None = Field(default=None, max_length=128)
    filled_price: float | None = None
    error: str | None = Field(default=None, max_length=1024)


class PositionSyncItem(BaseModel):
    user_id: str
    symbol: str = Field(..., min_length=1, max_length=32)
    ticket: str = Field(..., min_length=1, max_length=128)
    side: Literal["BUY", "SELL"]
    volume: float
    entry: float
    sl: float | None = None
    tp: float | None = None
    pnl: float | None = None
    status: Literal["OPEN", "TP", "TP1", "TP2", "SL", "CLOSED"] = "OPEN"
    reason: str | None = Field(default=None, max_length=512)
    price: float | None = None


class RunnerPositionSyncIn(BaseModel):
    runner_id: str = Field(..., min_length=1, max_length=64)
    positions: list[PositionSyncItem] = Field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _store_runner_heartbeat(
    payload: RunnerHeartbeatIn,
    *,
    db: Session,
    client_ip: str,
) -> dict:
    now_utc = datetime.now(timezone.utc)
    row = db.query(RunnerHeartbeat).filter(RunnerHeartbeat.runner_id == payload.runner_id).first()
    if not row:
        row = RunnerHeartbeat(runner_id=payload.runner_id)
        db.add(row)
    row.version = payload.version.strip()
    row.symbols_enabled_json = [s.strip().upper() for s in payload.symbols_enabled if s.strip()]
    row.last_ip = client_ip
    row.last_seen_at = now_utc
    db.add(row)

    status = db.query(RunnerStatus).filter(RunnerStatus.runner_id == payload.runner_id).first()
    if not status:
        status = RunnerStatus(runner_id=payload.runner_id, symbols_ok_json=[])
        db.add(status)
    if payload.mt5_connected is not None:
        status.mt5_connected = bool(payload.mt5_connected)
    if payload.last_tick_utc is not None:
        status.last_tick_utc = _as_utc(payload.last_tick_utc)
    if payload.last_ingest_utc is not None:
        status.last_ingest_utc = _as_utc(payload.last_ingest_utc)
    if payload.last_signal_utc is not None:
        status.last_signal_utc = _as_utc(payload.last_signal_utc)
    if payload.last_telegram_sent_utc is not None:
        status.last_telegram_sent_utc = _as_utc(payload.last_telegram_sent_utc)
    status.symbols_ok_json = [s.strip().upper() for s in payload.symbols_ok if s.strip()]
    status.last_error = (payload.last_error or "").strip() or None
    status.last_heartbeat_at = now_utc
    if bool(payload.mt5_connected) and status.last_error is None:
        status.last_ok_at = now_utc
    db.add(status)
    db.commit()
    return {
        "ok": True,
        "runner_id": row.runner_id,
        "last_seen_at": _iso(row.last_seen_at),
        "last_signal_utc": _iso(status.last_signal_utc),
        "last_telegram_sent_utc": _iso(status.last_telegram_sent_utc),
    }


@router.post("/heartbeat")
def runner_heartbeat(
    payload: RunnerHeartbeatIn,
    db: Session = Depends(get_db),
    client_ip: str = Depends(require_runner_auth),
    _limit: None = rate_limit("runner_heartbeat", (RateLimitRule(limit=120, window_seconds=60),)),
):
    return _store_runner_heartbeat(payload, db=db, client_ip=client_ip)


@router.post("/mt5/heartbeat")
def mt5_heartbeat(
    payload: RunnerHeartbeatIn,
    db: Session = Depends(get_db),
    client_ip: str = Depends(require_runner_auth),
    _limit: None = rate_limit("runner_mt5_heartbeat", (RateLimitRule(limit=120, window_seconds=60),)),
):
    return _store_runner_heartbeat(payload, db=db, client_ip=client_ip)


@router.get("/status")
def get_runner_status(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("runner_status_get", (RateLimitRule(limit=120, window_seconds=60),)),
):
    now_utc = datetime.now(timezone.utc)
    heartbeat = db.query(RunnerHeartbeat).order_by(RunnerHeartbeat.last_seen_at.desc()).first()

    status: RunnerStatus | None = None
    if heartbeat:
        status = db.query(RunnerStatus).filter(RunnerStatus.runner_id == heartbeat.runner_id).first()
    if status is None:
        status = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()

    last_signal_utc = status.last_signal_utc if status else None
    if last_signal_utc is None:
        last_signal_utc = db.query(func.max(LiquiditySignal.detected_at)).scalar()

    last_telegram_sent_utc = status.last_telegram_sent_utc if status else None
    if last_telegram_sent_utc is None:
        last_telegram_sent_utc = (
            db.query(func.max(SignalDelivery.sent_at))
            .filter(SignalDelivery.status == "sent")
            .scalar()
        )

    heartbeat_at = None
    if status and status.last_heartbeat_at:
        heartbeat_at = _as_utc(status.last_heartbeat_at)
    elif heartbeat and heartbeat.last_seen_at:
        heartbeat_at = _as_utc(heartbeat.last_seen_at)
    elif status and status.updated_at:
        heartbeat_at = _as_utc(status.updated_at)

    heartbeat_age_seconds = (
        max(int((now_utc - heartbeat_at).total_seconds()), 0) if heartbeat_at is not None else None
    )
    stale_after_seconds = max(int(settings.RUNNER_HEARTBEAT_STALE_SECONDS or 180), 30)
    runner_online = bool(
        status
        and status.mt5_connected
        and heartbeat_age_seconds is not None
        and heartbeat_age_seconds <= stale_after_seconds
    )

    return {
        "ok": True,
        "runner_id": heartbeat.runner_id if heartbeat else (status.runner_id if status else None),
        "runner_online": runner_online,
        "runner_status": "online" if runner_online else "offline",
        "mt5_connected": bool(status.mt5_connected) if status else False,
        "last_heartbeat_utc": _iso(heartbeat_at),
        "last_signal_utc": _iso(last_signal_utc),
        "last_telegram_sent_utc": _iso(last_telegram_sent_utc),
        "last_tick_utc": _iso(status.last_tick_utc) if status else None,
        "last_ingest_utc": _iso(status.last_ingest_utc) if status else None,
        "last_error": status.last_error if status else None,
        "heartbeat_age_seconds": heartbeat_age_seconds,
        "stale_after_seconds": stale_after_seconds,
        "symbols_ok": status.symbols_ok_json if (status and isinstance(status.symbols_ok_json, list)) else [],
        "session_independent": True,
    }


@router.get("/jobs/next")
def runner_next_job(
    runner_id: str,
    db: Session = Depends(get_db),
    _client_ip: str = Depends(require_runner_auth),
    _limit: None = rate_limit("runner_jobs_next", (RateLimitRule(limit=600, window_seconds=60),)),
):
    job = next_trade_job_for_runner(db, runner_id=runner_id.strip())
    db.commit()
    if not job:
        return Response(status_code=204)
    return {"ok": True, "job": job}


@router.post("/jobs/{job_id}/result")
def runner_job_result(
    job_id: UUID,
    payload: RunnerJobResultIn,
    db: Session = Depends(get_db),
    _client_ip: str = Depends(require_runner_auth),
    _limit: None = rate_limit("runner_job_result", (RateLimitRule(limit=300, window_seconds=60),)),
):
    result = submit_trade_job_result(
        db,
        job_id=job_id,
        status=payload.status,
        broker_ticket=payload.broker_ticket,
        filled_price=payload.filled_price,
        error=payload.error,
    )
    db.commit()
    return result


@router.post("/positions/sync")
def runner_positions_sync(
    payload: RunnerPositionSyncIn,
    db: Session = Depends(get_db),
    _client_ip: str = Depends(require_runner_auth),
    _limit: None = rate_limit("runner_positions_sync", (RateLimitRule(limit=240, window_seconds=60),)),
):
    result = sync_positions(db, rows=[item.model_dump() for item in payload.positions])
    db.commit()
    return {
        **result,
        "runner_id": payload.runner_id,
    }
