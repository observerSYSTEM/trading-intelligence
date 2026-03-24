from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(settings.JWT_EXPIRE_MINUTES)
JWT_EXPIRE_SECONDS = int(JWT_EXPIRE_MINUTES * 60)

def create_access_token(subject: str, expires_delta: Optional[timedelta] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=JWT_EXPIRE_MINUTES))
    payload: dict[str, object] = {
        "sub": subject,
        "iat": now,
        "nbf": now,
        "exp": expire,
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_access_token(token: str) -> dict:
    kwargs: dict[str, object] = {"algorithms": [JWT_ALGORITHM]}
    if settings.JWT_ISSUER:
        kwargs["issuer"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        kwargs["audience"] = settings.JWT_AUDIENCE
    return jwt.decode(token, settings.JWT_SECRET, **kwargs)
