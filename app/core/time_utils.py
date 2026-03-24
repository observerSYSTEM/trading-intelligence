from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _resolve_london_tz() -> tuple[timezone | ZoneInfo, bool]:
    try:
        return ZoneInfo("Europe/London"), True
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401

            return ZoneInfo("Europe/London"), True
        except Exception:
            return timezone.utc, False


LONDON_TZ, LONDON_TZ_AVAILABLE = _resolve_london_tz()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def london_now() -> datetime:
    return now_utc().astimezone(LONDON_TZ)


def london_0801_utc(for_date: date) -> datetime:
    local = datetime.combine(for_date, time(hour=8, minute=1, tzinfo=LONDON_TZ))
    return local.astimezone(timezone.utc)

