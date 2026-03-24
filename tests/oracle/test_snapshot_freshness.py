from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.oracle import _latest_snapshot, get_latest_oracle_snapshot_contract
from app.db.base import Base
from app.db.models import GoldRegimeDaily, MT5Candle, MT5IngestStatus, Subscription, User


@contextmanager
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db: Session = testing_session_local()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _mk_user(db: Session, email: str, *, plan: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, plan=plan, status="active"))
    db.commit()
    db.refresh(user)
    return user


def _add_snapshot(
    db: Session,
    *,
    symbol: str,
    as_of_utc: datetime,
    allowed_direction: str,
    confidence: float,
) -> None:
    db.add(
        GoldRegimeDaily(
            symbol=symbol,
            as_of_utc=as_of_utc,
            regime="bullish" if allowed_direction == "BUY_ONLY" else "bearish" if allowed_direction == "SELL_ONLY" else "range",
            confidence=confidence,
            allowed_direction=allowed_direction,
            final_allowed_basic=allowed_direction,
            final_allowed_elite=allowed_direction,
            daily_bias="bullish" if allowed_direction == "BUY_ONLY" else "neutral",
            confirm_ok=True,
            public_factors_json={
                "signal_timeframe": "M15",
                "confirm_timeframe": "H1",
                "daily_permission": allowed_direction,
                "opportunity_direction": allowed_direction,
                "confirm_ok": True,
                "reason_basic": "test snapshot",
            },
            internal_factors_json={},
            notes="test snapshot",
        )
    )


class SnapshotFreshnessTests(unittest.TestCase):
    def test_latest_snapshot_for_18_feb_selected(self):
        with test_db() as db:
            user = _mk_user(db, "elite-feb18@test.com", plan="elite")
            symbol = "XAUUSD"
            older = datetime(2026, 2, 18, 7, 0, tzinfo=timezone.utc)
            newer = datetime(2026, 2, 18, 8, 0, tzinfo=timezone.utc)

            _add_snapshot(db, symbol=symbol, as_of_utc=older, allowed_direction="BUY_ONLY", confidence=0.55)
            _add_snapshot(db, symbol=symbol, as_of_utc=newer, allowed_direction="SELL_ONLY", confidence=0.77)
            db.commit()

            payload = get_latest_oracle_snapshot_contract(symbol=symbol, user=user, db=db)
            self.assertEqual(payload["allowed_direction"], "SELL_ONLY")
            self.assertTrue(str(payload["as_of_utc"]).startswith("2026-02-18T08:00:00"))

    def test_latest_snapshot_is_selected_by_asof_desc(self):
        with test_db() as db:
            user = _mk_user(db, "elite-snap@test.com", plan="elite")
            symbol = "XAUUSD"
            older = datetime(2026, 2, 16, 9, 0, tzinfo=timezone.utc)
            newer = datetime(2026, 2, 16, 10, 0, tzinfo=timezone.utc)

            _add_snapshot(db, symbol=symbol, as_of_utc=older, allowed_direction="BUY_ONLY", confidence=0.51)
            _add_snapshot(db, symbol=symbol, as_of_utc=newer, allowed_direction="SELL_ONLY", confidence=0.73)
            db.commit()

            latest = _latest_snapshot(db, symbol)
            self.assertIsNotNone(latest)
            self.assertEqual(latest.as_of_utc.replace(tzinfo=timezone.utc), newer)
            self.assertEqual(latest.allowed_direction, "SELL_ONLY")

            payload = get_latest_oracle_snapshot_contract(symbol=symbol, user=user, db=db)
            self.assertTrue(str(payload["as_of_utc"]).startswith("2026-02-16T10:00:00"))
            self.assertEqual(payload["timeframe_main"], "M15")
            self.assertEqual(payload["timeframe_fast"], "M1")

    def test_missing_0801_does_not_mark_stale_if_m15_feed_is_fresh(self):
        with test_db() as db:
            user = _mk_user(db, "elite-fresh@test.com", plan="elite")
            symbol = "XAUUSD"
            now = datetime.now(timezone.utc)
            _add_snapshot(db, symbol=symbol, as_of_utc=now, allowed_direction="BUY_ONLY", confidence=0.6)
            db.add(
                MT5Candle(
                    symbol=symbol,
                    timeframe="M15",
                    time_utc=now,
                    open=2300.0,
                    high=2301.0,
                    low=2299.5,
                    close=2300.6,
                    volume=1000.0,
                )
            )
            db.add(MT5IngestStatus(symbol=symbol, last_ingested_at=now))
            db.commit()

            payload = get_latest_oracle_snapshot_contract(symbol=symbol, user=user, db=db)
            self.assertFalse(bool(payload.get("is_stale")))
            self.assertNotIn("missing_0801", payload.get("stale_reasons", []))

    def test_tier_enforcement_blocks_locked_symbol(self):
        with test_db() as db:
            user = _mk_user(db, "basic-snap@test.com", plan="basic")
            with self.assertRaises(HTTPException) as ctx:
                get_latest_oracle_snapshot_contract(symbol="GBPUSD", user=user, db=db)
            self.assertEqual(ctx.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
