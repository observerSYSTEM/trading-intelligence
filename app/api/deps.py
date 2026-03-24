import hmac

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.db.models import Subscription, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
oauth2_optional_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(token)
        email = payload.get("sub")
        if not email:
            raise ValueError("Missing subject")
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    setattr(
        user,
        "subscription_summary",
        {
            "plan": (sub.plan if sub else "basic") or "basic",
            "status": (sub.status if sub else "inactive") or "inactive",
            "is_active": bool(sub and (sub.status or "").lower() in ACTIVE_SUBSCRIPTION_STATUSES),
        },
    )
    return user


def get_current_user_optional(
    token: str | None = Depends(oauth2_optional_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    if not token:
        return None
    try:
        payload = decode_access_token(token)
        email = payload.get("sub")
        if not email:
            return None
    except Exception:
        return None

    user = db.query(User).filter(User.email == email).first()
    if not user or not user.is_active:
        return None

    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    setattr(
        user,
        "subscription_summary",
        {
            "plan": (sub.plan if sub else "basic") or "basic",
            "status": (sub.status if sub else "inactive") or "inactive",
            "is_active": bool(sub and (sub.status or "").lower() in ACTIVE_SUBSCRIPTION_STATUSES),
        },
    )
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if getattr(user, "role", "user") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user


def require_runner_auth(
    request: Request,
    x_runner_key: str | None = Header(default=None, alias="X-Runner-Key"),
) -> str:
    if not settings.RUNNER_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RUNNER_API_KEY is not configured",
        )
    provided_key = (x_runner_key or "").strip()
    expected_key = settings.RUNNER_API_KEY.strip()
    if not hmac.compare_digest(provided_key, expected_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid runner key")

    client_ip = request.client.host if request.client else ""
    if settings.RUNNER_TRUST_PROXY_HEADERS:
        forwarded_for = (request.headers.get("x-forwarded-for") or "").strip()
        if forwarded_for:
            forwarded_ip = forwarded_for.split(",")[0].strip()
            if forwarded_ip:
                client_ip = forwarded_ip

    allowlist = settings.runner_allowed_ips
    if settings.RUNNER_REQUIRE_IP_ALLOWLIST and not allowlist:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Runner IP allowlist is required but empty",
        )
    if allowlist and client_ip not in allowlist:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Runner IP not allowed")
    return client_ip
