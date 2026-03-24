from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.limits import PLAN_LIMITS
from app.db.models import Subscription, UsageLedger


class UsageLimitExceeded(RuntimeError):
    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(str(payload))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _add_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _first_of_next_month(now_utc: datetime) -> datetime:
    base = now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _add_month(base)


def _subtract_month(value: datetime) -> datetime:
    year = value.year - (1 if value.month == 1 else 0)
    month = 12 if value.month == 1 else value.month - 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _normalize_plan(plan: str | None) -> str:
    value = (plan or "basic").strip().lower()
    if value not in PLAN_LIMITS:
        return "basic"
    return value


def _monthly_limit(plan: str) -> int | None:
    return PLAN_LIMITS[_normalize_plan(plan)]["signals_per_month"]


@dataclass
class UsageWindow:
    start_utc: datetime
    end_utc: datetime


def get_or_create_subscription(db: Session, user_id) -> Subscription:
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub:
        return sub
    sub = Subscription(
        user_id=user_id,
        plan="basic",
        status="inactive",
        usage_count=0,
    )
    db.add(sub)
    db.flush()
    return sub


def resolve_usage_window(db: Session, sub: Subscription, now_utc: datetime | None = None) -> UsageWindow:
    now = _as_utc(now_utc or datetime.now(timezone.utc))
    changed = False

    if sub.usage_reset_at:
        end = _as_utc(sub.usage_reset_at)
        while now >= end:
            end = _add_month(end)
            changed = True
    else:
        end = _first_of_next_month(now)
        changed = True

    start = _subtract_month(end)
    if changed or sub.usage_reset_at is None:
        sub.usage_reset_at = end
        db.add(sub)
        db.flush()

    return UsageWindow(start_utc=start, end_utc=end)


def count_used_in_window(db: Session, user_id, window: UsageWindow) -> int:
    value = (
        db.query(func.coalesce(func.sum(UsageLedger.quantity), 0))
        .filter(UsageLedger.user_id == user_id)
        .filter(UsageLedger.created_at >= window.start_utc)
        .filter(UsageLedger.created_at < window.end_utc)
        .scalar()
    )
    return int(value or 0)


def get_usage(db: Session, user_id, *, now_utc: datetime | None = None) -> dict:
    sub = get_or_create_subscription(db, user_id)
    plan = _normalize_plan(sub.plan)
    window = resolve_usage_window(db, sub, now_utc=now_utc)
    used = count_used_in_window(db, user_id, window)
    limit = _monthly_limit(plan)
    remaining = None if limit is None else max(limit - used, 0)

    # Keep legacy field updated for compatibility while ledger is source of truth.
    sub.usage_count = used
    db.add(sub)
    db.flush()

    return {
        "tier": plan,
        "plan": plan,
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "resets_at": window.end_utc.isoformat(),
        "status": sub.status,
    }


def consume_usage(
    db: Session,
    user_id,
    *,
    n: int = 1,
    reason: str,
    symbol: str | None = None,
    signal_id: str | None = None,
    meta: dict | None = None,
    now_utc: datetime | None = None,
) -> dict:
    if n < 1:
        raise ValueError("n must be >= 1")

    event_time = _as_utc(now_utc or datetime.now(timezone.utc))
    usage = get_usage(db, user_id, now_utc=now_utc)
    limit = usage["limit"]
    used = int(usage["used"])
    if limit is not None and (used + n) > int(limit):
        raise UsageLimitExceeded(
            {
                "error": "usage_limit_exceeded",
                "tier": usage["tier"],
                "used": used,
                "limit": int(limit),
                "remaining": max(int(limit) - used, 0),
                "requested": n,
                "resets_at": usage["resets_at"],
            }
        )

    if signal_id:
        existing = (
            db.query(UsageLedger.id)
            .filter(UsageLedger.user_id == user_id, UsageLedger.signal_id == signal_id)
            .first()
        )
        if existing:
            return usage

    db.add(
        UsageLedger(
            user_id=user_id,
            tier=usage["tier"],
            symbol=(symbol or None),
            reason=reason[:128],
            signal_id=(signal_id[:128] if signal_id else None),
            quantity=n,
            meta_json=meta or {},
            created_at=event_time,
        )
    )
    db.flush()

    return get_usage(db, user_id, now_utc=now_utc)


def usage_snapshot_for_user(db: Session, user_id) -> tuple[Subscription, dict]:
    sub = get_or_create_subscription(db, user_id)
    payload = get_usage(db, user_id)
    return sub, payload


def can_consume_signal(db: Session, user_id, plan: str | None = None, now_utc: datetime | None = None) -> tuple[bool, dict]:
    usage = get_usage(db, user_id, now_utc=now_utc)
    limit = usage["limit"]
    if limit is None:
        return True, usage
    allowed = int(usage["remaining"] or 0) > 0
    return allowed, usage
