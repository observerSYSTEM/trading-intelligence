from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import enabled_symbols_from_settings
from app.db.models import MT5IngestStatus, User
from app.db.session import get_db

router = APIRouter(prefix="/oracle/mt5", tags=["oracle-mt5"])


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@router.get("/health")
def mt5_health(
    symbol: str | None = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    _limit: None = rate_limit("oracle_mt5_health", (RateLimitRule(limit=60, window_seconds=60),)),
):
    now_utc = datetime.now(timezone.utc)
    symbols = [symbol.strip().upper()] if symbol else enabled_symbols_from_settings()
    rows = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol.in_(symbols)).all()
    by_symbol = {row.symbol: row for row in rows}

    data: list[dict] = []
    for symbol in symbols:
        row = by_symbol.get(symbol)
        if not row:
            data.append({"symbol": symbol, "last_ingested_at": None, "lag_seconds": None})
            continue
        last = _as_utc(row.last_ingested_at)
        lag = max(int((now_utc - last).total_seconds()), 0)
        data.append({"symbol": symbol, "last_ingested_at": last.isoformat(), "lag_seconds": lag})

    if symbol:
        return data[0]
    return {"ok": True, "items": data}
