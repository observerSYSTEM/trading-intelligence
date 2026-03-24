from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.time_utils import LONDON_TZ, as_utc, london_0801_utc, london_now, now_utc
from app.db.models import MT5IngestStatus


@dataclass
class DailyPermissionTimeWindow:
    date_uk: date
    target_london_0801_utc: datetime
    broker_offset_seconds: int
    expected_0801_broker_utc: datetime
    search_start_broker_utc: datetime
    search_end_broker_utc: datetime


class TimeService:
    @staticmethod
    def now_utc() -> datetime:
        return now_utc()

    @staticmethod
    def london_now() -> datetime:
        return london_now()

    @staticmethod
    def broker_offset_seconds(db: Session, *, symbol: str) -> int:
        row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol.strip().upper()).first()
        if row is None or row.broker_offset_seconds is None:
            return 0
        try:
            return int(row.broker_offset_seconds)
        except Exception:
            return 0

    @staticmethod
    def latest_server_utc(db: Session, *, symbol: str) -> datetime:
        offset = TimeService.broker_offset_seconds(db, symbol=symbol)
        return now_utc() + timedelta(seconds=offset)

    @staticmethod
    def daily_permission_window_for_date(
        db: Session,
        *,
        symbol: str,
        for_date_uk: date,
        minutes_before: int = 3,
        minutes_after: int = 4,
    ) -> DailyPermissionTimeWindow:
        london_target_utc = london_0801_utc(for_date_uk)
        offset_seconds = TimeService.broker_offset_seconds(db, symbol=symbol)
        offset_delta = timedelta(seconds=offset_seconds)
        expected_broker_utc = london_target_utc + offset_delta
        search_start_utc = (london_target_utc - timedelta(minutes=max(int(minutes_before), 0))) + offset_delta
        search_end_utc = (london_target_utc + timedelta(minutes=max(int(minutes_after), 0))) + offset_delta + timedelta(
            minutes=1
        )
        return DailyPermissionTimeWindow(
            date_uk=for_date_uk,
            target_london_0801_utc=london_target_utc,
            broker_offset_seconds=offset_seconds,
            expected_0801_broker_utc=expected_broker_utc,
            search_start_broker_utc=search_start_utc,
            search_end_broker_utc=search_end_utc,
        )

    @staticmethod
    def london_time_display(dt_utc: datetime) -> str:
        return as_utc(dt_utc).astimezone(LONDON_TZ).isoformat()
