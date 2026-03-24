from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditLog, LoginAttempt


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        value = forwarded.split(",")[0].strip()
        if value:
            return value[:64]
    if request.client and request.client.host:
        return request.client.host[:64]
    return None


def user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:512]


def log_audit(
    db: Session,
    *,
    action: str,
    user_id=None,
    request: Request | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action[:128],
            ip=client_ip(request),
            user_agent=user_agent(request),
            meta_json=meta or {},
            ts=datetime.now(timezone.utc),
        )
    )


def log_login_attempt(
    db: Session,
    *,
    email: str | None,
    success: bool,
    request: Request | None = None,
    user_id=None,
    reason: str | None = None,
) -> None:
    db.add(
        LoginAttempt(
            ip=client_ip(request),
            email=(email or "").strip().lower()[:320],
            success=bool(success),
            user_agent=user_agent(request),
            reason=(reason or "")[:256] or None,
            ts=datetime.now(timezone.utc),
        )
    )
    log_audit(
        db,
        action="auth.login.success" if success else "auth.login.failed",
        user_id=user_id,
        request=request,
        meta={"email": (email or "").strip().lower(), "reason": reason},
    )
