from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Tuple

from sqlalchemy.orm import Session

from app.db.models import AccountActivationToken, User


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_activation_token(
    db: Session,
    *,
    user_id,
    ttl_minutes: int,
) -> str:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(int(ttl_minutes), 5))

    (
        db.query(AccountActivationToken)
        .filter(
            AccountActivationToken.user_id == user_id,
            AccountActivationToken.used_at.is_(None),
            AccountActivationToken.expires_at > now,
        )
        .update({"used_at": now}, synchronize_session=False)
    )

    plain_token = secrets.token_urlsafe(48)
    row = AccountActivationToken(
        user_id=user_id,
        token_hash=_hash_token(plain_token),
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    return plain_token


def consume_activation_token(db: Session, *, token: str) -> Tuple[User, AccountActivationToken] | None:
    now = datetime.now(timezone.utc)
    token_hash = _hash_token(token)
    row = (
        db.query(AccountActivationToken)
        .filter(AccountActivationToken.token_hash == token_hash)
        .first()
    )
    if not row:
        return None
    if row.used_at is not None:
        return None
    expires_at = row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return None

    user = db.query(User).filter(User.id == row.user_id).first()
    if not user or not user.is_active:
        return None
    row.used_at = now
    return user, row
