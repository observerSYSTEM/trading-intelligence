from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import MT5Candle
from app.services.oracle_engine import compute_hourly_candidate, compute_weekly_range_snapshot


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


def _add_candle(
    db: Session,
    *,
    symbol: str,
    timeframe: str,
    ts: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> None:
    db.add(
        MT5Candle(
            id=uuid.uuid4(),
            symbol=symbol,
            timeframe=timeframe,
            time_utc=ts,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )
    )


class WeeklyRangeRiskBannerTests(unittest.TestCase):
    def test_weekly_range_locks_after_24_h1_candles(self):
        with test_db() as db:
            week_start = datetime(2026, 2, 9, 0, 0, tzinfo=timezone.utc)  # Monday
            for i in range(24):
                ts = week_start + timedelta(hours=i)
                _add_candle(
                    db,
                    symbol="XAUUSD",
                    timeframe="H1",
                    ts=ts,
                    open_=2300 + i,
                    high=2302 + i,
                    low=2298 + i,
                    close=2301 + i,
                    volume=1000 + i,
                )
            db.commit()

            snapshot = compute_weekly_range_snapshot(
                db,
                symbol="XAUUSD",
                as_of_utc=week_start + timedelta(hours=23, minutes=59),
            )
            self.assertTrue(snapshot.range_ready)
            self.assertEqual(snapshot.meta_json.get("locked_by_candle_count"), True)

    def test_candidate_includes_blueprint_and_volume_spike_banner(self):
        with test_db() as db:
            monday = datetime(2026, 2, 9, 10, 0, tzinfo=timezone.utc)  # Monday
            # H1 candles for weekly/build context.
            for i in range(6):
                ts = monday - timedelta(hours=5 - i)
                _add_candle(
                    db,
                    symbol="XAUUSD",
                    timeframe="H1",
                    ts=ts,
                    open_=2300 + i,
                    high=2303 + i,
                    low=2298 + i,
                    close=2302 + i,
                    volume=1000,
                )
            # M15 candles with final spike.
            for i in range(20):
                ts = monday - timedelta(minutes=(20 - i) * 15)
                volume = 100.0
                if i == 19:
                    volume = 250.0  # 2.5x median -> spike
                _add_candle(
                    db,
                    symbol="XAUUSD",
                    timeframe="M15",
                    ts=ts,
                    open_=2300.0,
                    high=2301.0,
                    low=2299.0,
                    close=2300.5,
                    volume=volume,
                )
            db.commit()

            result = compute_hourly_candidate(db, symbol="XAUUSD")
            public = result.public_json
            risk_banner = public.get("risk_banner", {})
            weekly_range = public.get("weekly_range", {})

            self.assertTrue(risk_banner.get("is_blueprint_day"))
            self.assertTrue(risk_banner.get("volume_spike"))
            self.assertEqual(risk_banner.get("suggested_risk_multiplier"), 0.25)
            self.assertEqual(weekly_range.get("status"), "Building")
            self.assertEqual(weekly_range.get("range_ready"), False)


if __name__ == "__main__":
    unittest.main()

