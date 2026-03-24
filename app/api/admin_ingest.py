from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.db.models import User
from app.services.oracle_scheduler import run_market_ingest_job

router = APIRouter(prefix="/admin/ingest", tags=["admin-ingest"])


class IngestRunIn(BaseModel):
    timeframes: list[str] = Field(default_factory=lambda: ["M1", "M15", "H1"])


@router.post("/run")
def admin_ingest_run(
    payload: IngestRunIn,
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("admin_ingest_run", (RateLimitRule(limit=120, window_seconds=60),)),
):
    normalized: list[str] = []
    for value in payload.timeframes:
        tf = str(value).strip().upper()
        if tf and tf not in normalized:
            normalized.append(tf)
    if not normalized:
        normalized = ["M1", "M15", "H1"]
    return run_market_ingest_job(timeframes=normalized)
