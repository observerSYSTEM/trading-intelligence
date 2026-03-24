from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import MT5Candle
from app.services.oracle_engine import (
    compute_prelim_permission_from_asia,
    compute_daily_permission_from_m1,
    compute_opportunity_with_h1_confirmation,
)


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


def _seed_permission_candle(db: Session, *, symbol: str, target_utc: datetime, bullish: bool) -> None:
    o = 100.0
    c = 100.2 if bullish else 99.8
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M1",
            time_utc=target_utc,
            open=o,
            high=max(o, c) + 0.02,
            low=min(o, c) - 0.02,
            close=c,
            volume=1000,
        )
    )
    # baseline candles for range normalization
    for i in range(1, 25):
        t = target_utc - timedelta(minutes=i)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="M1",
                time_utc=t,
                open=100.0,
                high=100.05,
                low=99.95,
                close=100.01,
                volume=900,
            )
        )


def _seed_m15_and_h1_buy_setup(db: Session, *, symbol: str, now_utc: datetime) -> None:
    # H1 history + bullish confirmation candle
    for i in range(40):
        t = now_utc - timedelta(hours=40 - i)
        base = 100.0 + (i * 0.05)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="H1",
                time_utc=t,
                open=base,
                high=base + 0.4,
                low=base - 0.2,
                close=base + 0.25,
                volume=5000,
            )
        )

    # M15 history with breakout on last candle
    for i in range(20):
        t = now_utc - timedelta(minutes=(20 - i) * 15)
        base = 101.0 + (i * 0.02)
        close = base + 0.03
        high = max(base + 0.05, close + 0.01)
        if i == 19:
            close = base + 0.12
            high = close + 0.03
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="M15",
                time_utc=t,
                open=base,
                high=high,
                low=base - 0.04,
                close=close,
                volume=1500,
            )
        )


def _seed_asia_prelim_buy_case(db: Session, *, symbol: str, for_date: datetime) -> None:
    # Asia window baseline candles (00:00-05:45 London in winter = UTC for this test date)
    start = datetime(for_date.year, for_date.month, for_date.day, 0, 0, tzinfo=timezone.utc)
    for i in range(24):  # 6 hours of M15 bars
        t = start + timedelta(minutes=15 * i)
        base = 100.0 + (i * 0.02)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="M15",
                time_utc=t,
                open=base,
                high=base + 0.25,
                low=base - 0.25,
                close=base + 0.02,
                volume=1200,
            )
        )

    # Post-asia sellside sweep first, then bullish displacement above prior swing highs.
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M15",
            time_utc=datetime(for_date.year, for_date.month, for_date.day, 6, 15, tzinfo=timezone.utc),
            open=100.4,
            high=100.5,
            low=99.4,
            close=100.45,
            volume=1300,
        )
    )
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M15",
            time_utc=datetime(for_date.year, for_date.month, for_date.day, 6, 30, tzinfo=timezone.utc),
            open=100.45,
            high=101.2,
            low=100.4,
            close=101.15,
            volume=1800,
        )
    )


class DailyPermissionAlignmentTests(unittest.TestCase):
    def test_daily_permission_from_0801_m1(self):
        with test_db() as db:
            symbol = "XAUUSD"
            ref = datetime(2026, 2, 16, 8, 30, tzinfo=timezone.utc)
            target = datetime(2026, 2, 16, 8, 1, tzinfo=timezone.utc)
            _seed_permission_candle(db, symbol=symbol, target_utc=target, bullish=True)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            self.assertEqual(result.daily_permission, "BUY_ONLY")
            self.assertEqual(result.timeframe, "M1")
            self.assertEqual(result.daily_permission_stage, "OFFICIAL")
            self.assertTrue(result.official)
            self.assertEqual(result.permission_source, "LONDON_0801")

    def test_prelim_permission_from_asia_before_london_lock(self):
        with test_db() as db:
            symbol = "XAUUSD"
            ref = datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc)
            _seed_asia_prelim_buy_case(db, symbol=symbol, for_date=ref)
            db.commit()

            result = compute_prelim_permission_from_asia(db, symbol=symbol, ref_utc=ref)
            self.assertEqual(result.daily_permission_stage, "PRELIM")
            self.assertFalse(result.official)
            self.assertEqual(result.permission_source, "ASIA")
            self.assertEqual(result.daily_permission, "BUY_ONLY")
            self.assertGreater(result.confidence or 0.0, 0.5)

    def test_daily_permission_rolls_over_until_next_0801(self):
        with test_db() as db:
            symbol = "XAUUSD"
            ref = datetime(2026, 2, 16, 7, 55, tzinfo=timezone.utc)
            prev_target = datetime(2026, 2, 15, 8, 1, tzinfo=timezone.utc)
            _seed_permission_candle(db, symbol=symbol, target_utc=prev_target, bullish=False)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            self.assertEqual(result.daily_permission, "SELL_ONLY")
            self.assertEqual(result.as_of_utc, prev_target)

    def test_missing_0801_after_0820_marks_degraded_without_future_timestamp(self):
        with test_db() as db:
            symbol = "XAUUSD"
            ref = datetime(2026, 2, 16, 8, 25, tzinfo=timezone.utc)
            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            self.assertEqual(result.daily_permission, "NO_TRADE")
            self.assertLessEqual(result.as_of_utc, ref)
            self.assertTrue(bool(result.factors_json.get("missing_data")))
            self.assertTrue(bool(result.factors_json.get("degraded")))

    def test_opportunity_rejected_when_conflicts_with_daily_permission(self):
        with test_db() as db:
            symbol = "XAUUSD"
            now_utc = datetime(2026, 2, 16, 10, 15, tzinfo=timezone.utc)
            _seed_m15_and_h1_buy_setup(db, symbol=symbol, now_utc=now_utc)
            db.commit()

            opp = compute_opportunity_with_h1_confirmation(db, symbol=symbol, daily_permission="SELL_ONLY")
            self.assertEqual(opp.opportunity_direction, "BUY_ONLY")
            self.assertFalse(opp.aligned)
            self.assertEqual(opp.final_allowed, "NO_TRADE")

    def test_opportunity_allowed_when_aligned_and_h1_confirmed(self):
        with test_db() as db:
            symbol = "XAUUSD"
            now_utc = datetime(2026, 2, 16, 10, 15, tzinfo=timezone.utc)
            _seed_m15_and_h1_buy_setup(db, symbol=symbol, now_utc=now_utc)
            db.commit()

            opp = compute_opportunity_with_h1_confirmation(db, symbol=symbol, daily_permission="BUY_ONLY")
            self.assertTrue(opp.aligned)
            self.assertTrue(opp.h1_confirm_ok)
            self.assertEqual(opp.final_allowed, "BUY_ONLY")


if __name__ == "__main__":
    unittest.main()
