from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.db.session import get_db
from app.services.audit import log_audit
from app.services.autotrade_service import (
    set_global_autotrade_enabled,
    set_global_symbol_autotrade_enabled,
    set_user_autotrade_enabled,
)

router = APIRouter(prefix="/admin/autotrade", tags=["admin-autotrade"])


@router.post("/enable")
def admin_autotrade_enable(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_enable", (RateLimitRule(limit=30, window_seconds=60),)),
):
    result = set_global_autotrade_enabled(db, enabled=True)
    log_audit(db, action="admin.autotrade.enable", user_id=_admin.id, request=request, meta={"enabled": True})
    db.commit()
    return {"ok": True, **result}


@router.post("/disable")
def admin_autotrade_disable(
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_disable", (RateLimitRule(limit=30, window_seconds=60),)),
):
    result = set_global_autotrade_enabled(db, enabled=False)
    log_audit(db, action="admin.autotrade.disable", user_id=_admin.id, request=request, meta={"enabled": False})
    db.commit()
    return {"ok": True, **result}


@router.post("/user/{user_id}/enable")
def admin_autotrade_user_enable(
    user_id: UUID,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_user_enable", (RateLimitRule(limit=60, window_seconds=60),)),
):
    result = set_user_autotrade_enabled(db, user_id=user_id, enabled=True)
    log_audit(
        db,
        action="admin.autotrade.user.enable",
        user_id=_admin.id,
        request=request,
        meta={"target_user_id": str(user_id), "enabled": True},
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/user/{user_id}/disable")
def admin_autotrade_user_disable(
    user_id: UUID,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_user_disable", (RateLimitRule(limit=60, window_seconds=60),)),
):
    result = set_user_autotrade_enabled(db, user_id=user_id, enabled=False)
    log_audit(
        db,
        action="admin.autotrade.user.disable",
        user_id=_admin.id,
        request=request,
        meta={"target_user_id": str(user_id), "enabled": False},
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/symbol/{symbol}/enable")
def admin_autotrade_symbol_enable(
    symbol: str,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_symbol_enable", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value = symbol.strip().upper()
    result = set_global_symbol_autotrade_enabled(db, symbol=symbol_value, enabled=True)
    log_audit(
        db,
        action="admin.autotrade.symbol.enable",
        user_id=_admin.id,
        request=request,
        meta={"symbol": symbol_value, "enabled": True},
    )
    db.commit()
    return {"ok": True, **result}


@router.post("/symbol/{symbol}/disable")
def admin_autotrade_symbol_disable(
    symbol: str,
    request: Request,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("admin_autotrade_symbol_disable", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value = symbol.strip().upper()
    result = set_global_symbol_autotrade_enabled(db, symbol=symbol_value, enabled=False)
    log_audit(
        db,
        action="admin.autotrade.symbol.disable",
        user_id=_admin.id,
        request=request,
        meta={"symbol": symbol_value, "enabled": False},
    )
    db.commit()
    return {"ok": True, **result}

