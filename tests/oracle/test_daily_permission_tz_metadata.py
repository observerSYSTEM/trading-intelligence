from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.time_utils import LONDON_TZ_AVAILABLE, london_0801_utc
from app.db.base import Base
from app.db.models import MT5Candle, MT5IngestStatus
from app.services.oracle_engine import compute_daily_permission_from_m1


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


def _seed_0801_candle(db: Session, *, symbol: str, target_utc: datetime, bullish: bool) -> None:
    open_ = 100.0
    close = 100.2 if bullish else 99.8
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M1",
            time_utc=target_utc,
            open=open_,
            high=max(open_, close) + 0.02,
            low=min(open_, close) - 0.02,
            close=close,
            volume=1000,
        )
    )
    for i in range(1, 15):
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
                volume=850,
            )
        )


def _seed_single_m1_candle(
    db: Session,
    *,
    symbol: str,
    candle_utc: datetime,
    bullish: bool,
) -> None:
    open_ = 100.0
    close = 100.2 if bullish else 99.8
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M1",
            time_utc=candle_utc,
            open=open_,
            high=max(open_, close) + 0.02,
            low=min(open_, close) - 0.02,
            close=close,
            volume=1000,
        )
    )


def _seed_baseline_history(
    db: Session,
    *,
    symbol: str,
    anchor_utc: datetime,
) -> None:
    for i in range(10, 24):
        t = anchor_utc - timedelta(minutes=i)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="M1",
                time_utc=t,
                open=100.0,
                high=100.05,
                low=99.95,
                close=100.01,
                volume=850,
            )
        )


@unittest.skipUnless(LONDON_TZ_AVAILABLE, "Europe/London timezone data is required")
class DailyPermissionTimezoneTests(unittest.TestCase):
    def test_london_0801_utc_handles_dst_and_standard_time(self):
        winter = london_0801_utc(date(2026, 1, 15))
        summer = london_0801_utc(date(2026, 7, 15))

        self.assertEqual((winter.hour, winter.minute), (8, 1))
        self.assertEqual((summer.hour, summer.minute), (7, 1))
        self.assertEqual(winter.tzinfo, timezone.utc)
        self.assertEqual(summer.tzinfo, timezone.utc)

    def test_daily_permission_stores_metadata_fields(self):
        with test_db() as db:
            symbol = "XAUUSD"
            target = london_0801_utc(date(2026, 2, 18))
            ref = target + timedelta(minutes=29)
            _seed_0801_candle(db, symbol=symbol, target_utc=target, bullish=True)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            factors = result.factors_json if isinstance(result.factors_json, dict) else {}

            self.assertEqual(result.daily_permission, "BUY_ONLY")
            self.assertEqual(factors.get("permission_date"), "2026-02-18")
            self.assertEqual(factors.get("permission_value"), "BUY_ONLY")
            self.assertEqual(factors.get("permission_candle_close_utc"), target.isoformat())
            self.assertEqual(factors.get("requested_symbol"), symbol)
            self.assertEqual(factors.get("resolved_mt5_symbol"), symbol)
            self.assertEqual(factors.get("selection_source"), "utc_exact")
            self.assertEqual(factors.get("selected_time_delta_seconds"), 0)
            self.assertEqual(factors.get("selection_tolerance_seconds"), 60)
            self.assertTrue(str(factors.get("lookup_start_utc") or "").endswith("07:56:00+00:00"))
            self.assertTrue(str(factors.get("lookup_end_utc") or "").endswith("08:07:00+00:00"))
            self.assertGreaterEqual(int(factors.get("m1_candles_returned_utc_window") or 0), 1)
            self.assertEqual(factors.get("stale_reasons"), [])

    def test_missing_0801_exposes_stale_reasons(self):
        with test_db() as db:
            symbol = "XAUUSD"
            ref = datetime(2026, 2, 18, 8, 25, tzinfo=timezone.utc)
            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            factors = result.factors_json if isinstance(result.factors_json, dict) else {}

            self.assertEqual(result.daily_permission, "NO_TRADE")
            self.assertIn("missing_0801", factors.get("stale_reasons", []))
            self.assertEqual(factors.get("permission_date"), "2026-02-18")
            self.assertEqual(factors.get("permission_value"), "NO_TRADE")

    def test_daily_permission_resolves_via_broker_offset_window(self):
        with test_db() as db:
            symbol = "XAUUSD"
            # London 08:01 UTC (winter) mapped to broker +2h => 10:01 stored candle time.
            target_london = london_0801_utc(date(2026, 2, 19))
            broker_offset = 2 * 3600
            broker_time = target_london + timedelta(seconds=broker_offset)
            ref = datetime(2026, 2, 19, 8, 25, tzinfo=timezone.utc)

            db.add(
                MT5IngestStatus(
                    symbol=symbol,
                    last_ingested_at=broker_time,
                    broker_offset_seconds=broker_offset,
                    broker_offset_detected_at=ref,
                )
            )
            _seed_0801_candle(db, symbol=symbol, target_utc=broker_time, bullish=True)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            factors = result.factors_json if isinstance(result.factors_json, dict) else {}

            self.assertEqual(result.daily_permission, "BUY_ONLY")
            self.assertEqual(result.permission_source, "LONDON_0801")
            self.assertEqual(factors.get("broker_offset_seconds"), broker_offset)
            self.assertEqual(factors.get("expected_0801_broker_time"), broker_time.isoformat())
            self.assertEqual(factors.get("actual_candle_found_time"), broker_time.isoformat())
            self.assertEqual(factors.get("permission_candle_close_utc"), target_london.isoformat())
            self.assertEqual(factors.get("selection_source"), "broker_exact")
            self.assertEqual(factors.get("selected_time_delta_seconds"), 0)
            self.assertGreaterEqual(int(factors.get("m1_candles_returned_broker_window") or 0), 1)
            self.assertEqual(factors.get("nearest_available_candle_time"), broker_time.isoformat())

    def test_daily_permission_selects_nearest_valid_candle_within_tolerance(self):
        with test_db() as db:
            symbol = "GBPJPY"
            target = london_0801_utc(date(2026, 2, 20))
            ref = target + timedelta(minutes=24)
            nearest = target + timedelta(minutes=1)

            _seed_single_m1_candle(db, symbol=symbol, candle_utc=nearest, bullish=True)
            _seed_baseline_history(db, symbol=symbol, anchor_utc=target)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            factors = result.factors_json if isinstance(result.factors_json, dict) else {}

            self.assertEqual(result.daily_permission, "BUY_ONLY")
            self.assertEqual(factors.get("selection_source"), "utc_nearest_within_tolerance")
            self.assertEqual(factors.get("selected_time_delta_seconds"), 60)
            self.assertEqual(factors.get("actual_candle_found_time"), nearest.isoformat())
            self.assertEqual(factors.get("permission_candle_close_utc"), nearest.isoformat())
            self.assertEqual(factors.get("nearest_available_candle_time"), nearest.isoformat())
            self.assertEqual(factors.get("nearest_available_candle_delta_seconds"), 60)

    def test_daily_permission_reports_nearest_outside_tolerance_without_selecting_it(self):
        with test_db() as db:
            symbol = "XAUUSD"
            target = london_0801_utc(date(2026, 2, 21))
            ref = target + timedelta(minutes=24)
            nearest = target + timedelta(minutes=2)

            _seed_single_m1_candle(db, symbol=symbol, candle_utc=nearest, bullish=True)
            _seed_baseline_history(db, symbol=symbol, anchor_utc=target)
            db.commit()

            result = compute_daily_permission_from_m1(db, symbol=symbol, ref_utc=ref)
            factors = result.factors_json if isinstance(result.factors_json, dict) else {}

            self.assertEqual(result.daily_permission, "NO_TRADE")
            self.assertEqual(factors.get("selection_source"), "nearest_outside_tolerance")
            self.assertIsNone(factors.get("actual_candle_found_time"))
            self.assertIsNone(factors.get("permission_candle_close_utc"))
            self.assertEqual(factors.get("nearest_available_candle_time"), nearest.isoformat())
            self.assertEqual(factors.get("nearest_available_candle_delta_seconds"), 120)
            self.assertEqual(factors.get("selection_tolerance_seconds"), 60)
            self.assertIn("missing_0801", factors.get("stale_reasons", []))


if __name__ == "__main__":
    unittest.main()
