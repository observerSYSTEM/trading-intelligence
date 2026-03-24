from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SignalDirection = Literal["BUY", "SELL", "BUY_ONLY", "SELL_ONLY", "NO_TRADE", "NEUTRAL"]
SignalBias = Literal["BUY_ONLY", "SELL_ONLY", "NO_TRADE", "BULLISH", "BEARISH", "NEUTRAL"]


class SignalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(..., min_length=2, max_length=20)
    timeframe: str = Field(..., min_length=1, max_length=10)
    signal_type: str = Field(..., min_length=2, max_length=40)
    direction: SignalDirection | None = None
    magnet: float | None = None
    magnet_level: float | None = None
    price: float | None = None
    bias: SignalBias | None = None
    reason: str | None = Field(default=None, max_length=512)
    confidence: float | None = None
    daily_permission: SignalBias | None = None
    h1_confirmation: str | bool | None = None
    zone_target: float | None = None
    sellside_liquidity: float | None = None
    buyside_liquidity: float | None = None
    source: str = Field(default="pi", min_length=1, max_length=64)
    detected_at: datetime
    meta: dict[str, Any] | None = None
    dedup_key: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("timeframe")
    @classmethod
    def normalize_timeframe(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("signal_type")
    @classmethod
    def normalize_signal_type(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        return clean or None

    @field_validator("h1_confirmation", mode="before")
    @classmethod
    def normalize_h1_confirmation(cls, value: str | bool | None) -> str | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return "CONFIRMED" if value else "NOT_CONFIRMED"
        clean = str(value).strip().upper()
        if not clean:
            return None
        if clean in {"TRUE", "YES", "OK", "CONFIRMED"}:
            return "CONFIRMED"
        if clean in {"FALSE", "NO", "REJECTED", "NOT_CONFIRMED"}:
            return "NOT_CONFIRMED"
        return clean

    @field_validator("detected_at")
    @classmethod
    def ensure_timezone_aware_detected_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class SignalOut(BaseModel):
    id: str
    symbol: str
    timeframe: str
    type: str
    signal_type: str
    direction: str | None = None
    magnet: float | None = None
    magnet_level: float | None = None
    price: float | None = None
    bias: str | None = None
    reason: str | None = None
    confidence: float | None = None
    daily_permission: str | None = None
    h1_confirmation: str | None = None
    zone_target: float | None = None
    sellside_liquidity: float | None = None
    buyside_liquidity: float | None = None
    source: str
    detected_at: datetime
    meta: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str
    created_at: datetime


class SignalCreateResult(BaseModel):
    signal: SignalOut
    duplicate: bool = False


class SignalListOut(BaseModel):
    items: list[SignalOut]
    total: int
    limit: int
    offset: int
