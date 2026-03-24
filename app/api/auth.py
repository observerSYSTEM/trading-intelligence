from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.security import JWT_EXPIRE_SECONDS, create_access_token, hash_password, verify_password
from app.db.models import AccountActivationToken, Subscription, User, UserSignalPref
from app.db.session import get_db
from app.schemas.auth import (
    AuthMeResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    SetPasswordRequest,
    TokenResponse,
)
from app.services.account_activation import consume_activation_token
from app.services.audit import log_audit, log_login_attempt
from app.services.refresh_tokens import issue_refresh_token, revoke_refresh_token, rotate_refresh_token

router = APIRouter(prefix="/auth", tags=["auth"])


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _issue_tokens(db: Session, *, user: User) -> TokenResponse:
    access_token = create_access_token(subject=user.email)
    refresh_token = issue_refresh_token(
        db,
        user_id=user.id,
        ttl_days=settings.JWT_REFRESH_EXPIRE_DAYS,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=JWT_EXPIRE_SECONDS,
    )


@router.post("/register", response_model=TokenResponse)
def register(
    payload: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "auth_register",
        (
            RateLimitRule(limit=5, window_seconds=60),
            RateLimitRule(limit=20, window_seconds=3600),
        ),
    ),
):
    if not settings.ALLOW_PUBLIC_REGISTRATION:
        raise HTTPException(
            status_code=403,
            detail="Public registration is disabled. Start from Pricing checkout.",
        )

    email = _normalize_email(str(payload.email))
    full_name = " ".join(payload.full_name.strip().split())

    existing = db.query(User).filter(func.lower(User.email) == email).first()
    if existing:
        log_audit(
            db,
            action="auth.register.duplicate_email",
            request=request,
            meta={"email": email},
        )
        db.commit()
        raise HTTPException(status_code=409, detail="Email is already registered")

    user = User(
        full_name=full_name,
        email=email,
        password_hash=hash_password(payload.password),
        role="user",
        is_active=True,
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email is already registered")

    db.add(
        Subscription(
            user_id=user.id,
            plan="basic",
            status="inactive",
        )
    )
    db.add(
        UserSignalPref(
            user_id=user.id,
            symbols_json=["XAUUSD"],
            telegram_enabled=False,
            telegram_chat_id=None,
        )
    )
    log_audit(
        db,
        action="auth.register.success",
        user_id=user.id,
        request=request,
        meta={"email": email},
    )
    tokens = _issue_tokens(db, user=user)
    db.commit()
    return tokens


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "auth_login",
        (
            RateLimitRule(limit=5, window_seconds=60),
            RateLimitRule(limit=20, window_seconds=3600),
        ),
    ),
):
    email = _normalize_email(str(payload.email))
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user or not user.is_active:
        log_login_attempt(db, email=email, success=False, request=request, reason="invalid_user")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not (user.password_hash or "").strip():
        log_login_attempt(db, email=email, success=False, request=request, user_id=user.id, reason="password_not_set")
        db.commit()
        raise HTTPException(status_code=403, detail="Account setup required")
    if not verify_password(payload.password, user.password_hash):
        log_login_attempt(db, email=email, success=False, request=request, user_id=user.id, reason="invalid_password")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    log_login_attempt(db, email=email, success=True, request=request, user_id=user.id, reason="ok")
    tokens = _issue_tokens(db, user=user)
    db.commit()
    return tokens


@router.post("/token", response_model=TokenResponse)
def token(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "auth_token",
        (
            RateLimitRule(limit=5, window_seconds=60),
            RateLimitRule(limit=20, window_seconds=3600),
        ),
    ),
):
    email = _normalize_email(form.username)
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if not user or not user.is_active:
        log_login_attempt(db, email=email, success=False, request=request, reason="invalid_user")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not (user.password_hash or "").strip():
        log_login_attempt(db, email=email, success=False, request=request, user_id=user.id, reason="password_not_set")
        db.commit()
        raise HTTPException(status_code=403, detail="Account setup required")
    if not verify_password(form.password, user.password_hash):
        log_login_attempt(db, email=email, success=False, request=request, user_id=user.id, reason="invalid_password")
        db.commit()
        raise HTTPException(status_code=401, detail="Invalid credentials")
    log_login_attempt(db, email=email, success=True, request=request, user_id=user.id, reason="ok")
    tokens = _issue_tokens(db, user=user)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(
    payload: RefreshRequest,
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "auth_refresh",
        (
            RateLimitRule(limit=30, window_seconds=60),
            RateLimitRule(limit=600, window_seconds=3600),
        ),
    ),
):
    rotated = rotate_refresh_token(
        db,
        token=payload.refresh_token,
        ttl_days=settings.JWT_REFRESH_EXPIRE_DAYS,
    )
    if rotated is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user, new_refresh_token = rotated
    access_token = create_access_token(subject=user.email)
    log_audit(
        db,
        action="auth.refresh.success",
        user_id=user.id,
        request=request,
        meta={"email": user.email},
    )
    db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=JWT_EXPIRE_SECONDS,
    )


@router.post("/logout")
def logout(
    payload: LogoutRequest,
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit("auth_logout", (RateLimitRule(limit=60, window_seconds=60),)),
):
    refresh_token = (payload.refresh_token or "").strip()
    revoked = False
    if refresh_token:
        revoked = revoke_refresh_token(db, token=refresh_token)

    log_audit(
        db,
        action="auth.logout",
        request=request,
        meta={"refresh_revoked": revoked},
    )
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=AuthMeResponse)
def auth_me(user: User = Depends(get_current_user)):
    return AuthMeResponse(
        id=str(user.id),
        full_name=(user.full_name or "").strip(),
        email=user.email,
        role=user.role,
        is_active=bool(user.is_active),
        created_at=user.created_at,
    )


@router.post("/set-password", response_model=TokenResponse)
def set_password(
    payload: SetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "auth_set_password",
        (
            RateLimitRule(limit=15, window_seconds=60),
            RateLimitRule(limit=120, window_seconds=3600),
        ),
    ),
):
    consumed = consume_activation_token(db, token=payload.token)
    if consumed is None:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token")

    user, _token_row = consumed
    user.password_hash = hash_password(payload.password)

    db.query(AccountActivationToken).filter(
        AccountActivationToken.user_id == user.id,
        AccountActivationToken.used_at.is_(None),
    ).update({"used_at": datetime.now(timezone.utc)}, synchronize_session=False)

    log_audit(
        db,
        action="auth.set_password.success",
        user_id=user.id,
        request=request,
        meta={"email": user.email},
    )
    tokens = _issue_tokens(db, user=user)
    db.commit()
    return tokens
