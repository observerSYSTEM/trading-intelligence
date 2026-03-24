from __future__ import annotations

from datetime import date, datetime

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.db.session import get_db
from app.services.audit import log_audit
from app.services.oracle_scheduler import run_daily_audit_now, run_price_monitor_now
from app.services.runner_control import trigger_runner_reconnect
from app.services.stripe import validate_price_catalog
from app.services.strategy_matrix import get_active_strategy_matrix
from app.services.telegram_service import ensure_pinned_bias, send_reply

router = APIRouter(prefix="/admin/ops", tags=["admin-ops"])


def _check_alembic_head(db: Session) -> dict:
    try:
        rows = db.execute(text("SELECT version_num FROM alembic_version")).fetchall()
        current = sorted(str(r[0]) for r in rows if r and r[0])
        cfg = AlembicConfig("alembic.ini")
        heads = sorted(ScriptDirectory.from_config(cfg).get_heads())
        ok = bool(current) and set(current) == set(heads)
        return {"ok": ok, "current": current, "heads": heads}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "current": [], "heads": []}


def build_readiness_payload(db: Session) -> dict:
    db_check = {"ok": True}
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        db_check = {"ok": False, "error": str(exc)}

    stripe_secret_present = bool(settings.STRIPE_SECRET_KEY.strip())
    stripe_webhook_secret_present = bool(settings.STRIPE_WEBHOOK_SECRET.strip())
    stripe_price_ids_present = bool(settings.stripe_price_basic and settings.stripe_price_pro and settings.stripe_price_elite)
    stripe_env_present = stripe_secret_present and stripe_webhook_secret_present
    stripe_price_checks = {}
    if stripe_secret_present and stripe_price_ids_present:
        stripe_price_checks = validate_price_catalog()
    stripe_mode_check_ok = (
        stripe_secret_present
        and stripe_price_ids_present
        and bool(stripe_price_checks)
        and all(v == "ok" for v in stripe_price_checks.values())
    )

    cors_origins = settings.cors_origins
    cors_configured = bool(cors_origins) and all(origin.strip() != "*" for origin in cors_origins)
    debug_off_in_prod = (not settings.is_production) or (settings.is_production and not settings.docs_enabled)

    checks = {
        "db_reachable": db_check,
        "alembic_head": _check_alembic_head(db),
        "stripe_env_present": {
            "ok": stripe_env_present,
            "secret_present": stripe_secret_present,
            "webhook_secret_present": stripe_webhook_secret_present,
        },
        "stripe_price_ids_present": {
            "ok": stripe_price_ids_present,
            "basic": bool(settings.stripe_price_basic),
            "pro": bool(settings.stripe_price_pro),
            "elite": bool(settings.stripe_price_elite),
        },
        "stripe_key_mode": {"ok": settings.stripe_key_mode in {"test", "live"}, "mode": settings.stripe_key_mode},
        "stripe_price_ids_match_key_mode": {
            "ok": stripe_mode_check_ok,
            "details": stripe_price_checks,
        },
        "telegram_env_present": {"ok": bool(settings.TELEGRAM_BOT_TOKEN.strip())},
        "runner_api_key_present": {"ok": bool(settings.RUNNER_API_KEY.strip())},
        "cors_configured": {"ok": cors_configured, "origins": cors_origins},
        "debug_off_in_prod": {"ok": debug_off_in_prod, "docs_enabled": settings.docs_enabled},
    }
    overall_ok = all(bool(item.get("ok")) for item in checks.values())
    return {
        "ok": overall_ok,
        "env": settings.APP_ENV,
        "checks": checks,
    }


@router.get("/readiness")
def readiness(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_ops_readiness", (RateLimitRule(limit=30, window_seconds=60),)),
):
    return build_readiness_payload(db)


@router.get("/strategy-matrix")
def get_strategy_matrix(
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("admin_ops_strategy_matrix", (RateLimitRule(limit=60, window_seconds=60),)),
):
    return {"ok": True, **get_active_strategy_matrix()}


class ThreadTestIn(BaseModel):
    chat_id: str = Field(..., min_length=1, max_length=128)
    symbol: str = Field(default="XAUUSD", min_length=1, max_length=32)
    date_uk: date | None = None
    text: str = Field(default="Thread test message", min_length=1, max_length=1000)
    pin_daily_bias: bool = True


class RunnerReconnectIn(BaseModel):
    reason: str = Field(default="admin_manual", min_length=1, max_length=200)


@router.post("/runner/reconnect")
def runner_reconnect(
    payload: RunnerReconnectIn,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_ops_runner_reconnect", (RateLimitRule(limit=20, window_seconds=60),)),
):
    result = trigger_runner_reconnect(reason=payload.reason)
    if not bool(result.get("configured")):
        raise HTTPException(status_code=503, detail=result.get("error") or "Runner control URL is not configured.")
    if not bool(result.get("ok")):
        raise HTTPException(status_code=502, detail=result.get("error") or "Runner reconnect failed.")

    log_audit(
        db,
        action="admin.ops.runner.reconnect",
        user_id=_admin.id,
        meta={"reason": payload.reason, "runner_response": result.get("data")},
    )
    db.commit()
    return {
        "ok": True,
        "reason": payload.reason,
        "runner": result.get("data"),
    }


@router.post("/telegram/thread-test")
def telegram_thread_test(
    payload: ThreadTestIn,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_ops_telegram_thread_test", (RateLimitRule(limit=20, window_seconds=60),)),
):
    target_date = payload.date_uk or datetime.utcnow().date()
    symbol = payload.symbol.strip().upper()
    anchor_text = f"DAILY BIAS - {symbol}\nDate: {target_date.isoformat()}\nThread: Daily intelligence updates."
    anchor_id = ensure_pinned_bias(
        db,
        chat_id=payload.chat_id.strip(),
        symbol=symbol,
        date_uk=target_date,
        anchor_text=anchor_text,
        pin_bool=bool(payload.pin_daily_bias),
    )
    message_id = send_reply(payload.chat_id.strip(), anchor_id, payload.text.strip())
    log_audit(
        db,
        action="admin.ops.telegram.thread_test",
        user_id=_admin.id,
        meta={"chat_id": payload.chat_id.strip(), "symbol": symbol, "date_uk": target_date.isoformat()},
    )
    db.commit()
    return {"ok": True, "chat_id": payload.chat_id.strip(), "symbol": symbol, "date_uk": target_date.isoformat(), "pinned_message_id": anchor_id, "reply_message_id": message_id}


@router.post("/jobs/price-monitor-now")
def run_price_monitor_job_now(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_ops_price_monitor", (RateLimitRule(limit=20, window_seconds=60),)),
):
    result = run_price_monitor_now()
    log_audit(db, action="admin.ops.jobs.price_monitor_now", user_id=_admin.id, meta=result)
    db.commit()
    return {"ok": True, **result}


@router.post("/jobs/daily-audit-now")
def run_daily_audit_job_now(
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_ops_daily_audit", (RateLimitRule(limit=20, window_seconds=60),)),
):
    result = run_daily_audit_now()
    log_audit(db, action="admin.ops.jobs.daily_audit_now", user_id=_admin.id, meta=result)
    db.commit()
    return {"ok": True, **result}
