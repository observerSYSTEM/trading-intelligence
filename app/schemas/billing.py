from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CheckoutSessionIn(BaseModel):
    plan: Literal["basic", "pro", "elite"]
    email: EmailStr | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=120)


class CheckoutActivationIn(BaseModel):
    session_id: str = Field(..., min_length=5, max_length=255)


class CheckoutActivationOut(BaseModel):
    ready: bool
    requires_password_setup: bool
    message: str
    email: EmailStr | None = None
    activation_token: str | None = None
    expires_in_seconds: int | None = None


class PortalSessionOut(BaseModel):
    url: str = Field(..., min_length=1, max_length=4096)
