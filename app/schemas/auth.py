from __future__ import annotations

from datetime import datetime
import re

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

_PASSWORD_UPPER_RE = re.compile(r"[A-Z]")
_PASSWORD_LOWER_RE = re.compile(r"[a-z]")
_PASSWORD_DIGIT_RE = re.compile(r"\d")


def _validate_password_strength(value: str) -> str:
    if not _PASSWORD_UPPER_RE.search(value):
        raise ValueError("Password must include at least one uppercase letter.")
    if not _PASSWORD_LOWER_RE.search(value):
        raise ValueError("Password must include at least one lowercase letter.")
    if not _PASSWORD_DIGIT_RE.search(value):
        raise ValueError("Password must include at least one number.")
    return value


class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)
    confirm_password: str = Field(..., min_length=8, max_length=256)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if len(cleaned) < 2:
            raise ValueError("Full name is required.")
        return cleaned

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)

    @model_validator(mode="after")
    def ensure_passwords_match(self) -> "RegisterRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=256)


class SetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=1024)
    password: str = Field(..., min_length=8, max_length=256)
    confirm_password: str = Field(..., min_length=8, max_length=256)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password_strength(value)

    @model_validator(mode="after")
    def ensure_passwords_match(self) -> "SetPasswordRequest":
        if self.password != self.confirm_password:
            raise ValueError("Passwords do not match.")
        return self


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=24, max_length=2048)


class LogoutRequest(BaseModel):
    refresh_token: str | None = Field(default=None, min_length=24, max_length=2048)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    token_type: str = "bearer"


class AuthMeResponse(BaseModel):
    id: str
    full_name: str
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime | None = None
