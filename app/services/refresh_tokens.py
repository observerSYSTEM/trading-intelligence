from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Tuple

from sqlalchemy.orm import Session

from app.db.models import RefreshToken, User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh_token(
    db: Session,
    *,
    user_id,
    ttl_days: int,
) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=max(int(ttl_days), 1))
    plain_token = secrets.token_urlsafe(64)
    row = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(plain_token),
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return plain_token


def rotate_refresh_token(
    db: Session,
    *,
    token: str,
    ttl_days: int,
) -> Tuple[User, str] | None:
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(token)
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )
    if not row or row.revoked_at is not None:
        return None

    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        row.revoked_at = now
        return None

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or not user.is_active:
        row.revoked_at = now
        return None

    new_token = issue_refresh_token(db, user_id=user.id, ttl_days=ttl_days)
    row.revoked_at = now
    row.replaced_by_token_hash = _hash_token(new_token)
    return user, new_token


def revoke_refresh_token(
    db: Session,
    *,
    token: str,
) -> bool:
    token_hash = _hash_token(token)
    row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )
    if not row or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.now(timezone.utc)
    return True
