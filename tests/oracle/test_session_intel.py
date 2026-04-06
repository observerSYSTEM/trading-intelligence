from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.oracle import get_oracle_session_context
from app.core.symbols import get_symbol_market_config
from app.core.time_utils import LONDON_TZ
from app.db.base import Base
from app.db.models import MT5Candle, User, UserSignalPref
from app.services.session_intel import calculate_range_size_pips, get_symbol_session_context, resolve_session_state


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


def _seed_candle(
    db: Session,
    *,
    symbol: str,
    london_time: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    timeframe: str = "M1",
) -> None:
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe=timeframe,
            time_utc=london_time.astimezone(timezone.utc),
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=100,
        )
    )


def _get_context_for_anchor(
    db: Session,
    *,
    symbol: str,
    london_day: datetime,
    as_of_hour: int = 9,
) -> dict:
    return get_symbol_session_context(
        db,
        symbol=symbol,
        as_of_utc=london_day.replace(hour=as_of_hour, minute=0, second=0, microsecond=0).astimezone(timezone.utc),
    )


def _get_context_for_sweep(
    db: Session,
    *,
    symbol: str,
    london_day: datetime,
    as_of_hour: int = 10,
    as_of_minute: int = 0,
) -> dict:
    return get_symbol_session_context(
        db,
        symbol=symbol,
        as_of_utc=london_day.replace(
            hour=as_of_hour,
            minute=as_of_minute,
            second=0,
            microsecond=0,
        ).astimezone(timezone.utc),
    )


def _seed_default_asian_range(db: Session, *, symbol: str, london_day: datetime) -> None:
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=0, minute=10),
        open_=196.05,
        high=196.10,
        low=196.02,
        close=196.08,
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=2, minute=25),
        open_=196.12,
        high=196.30,
        low=196.10,
        close=196.24,
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=6, minute=50),
        open_=196.06,
        high=196.08,
        low=196.00,
        close=196.03,
    )


def _seed_previous_day_range(
    db: Session,
    *,
    symbol: str,
    london_day: datetime,
    high: float,
    low: float,
) -> None:
    previous_day = london_day - timedelta(days=1)
    midpoint = round((high + low) / 2.0, 5)
    _seed_candle(
        db,
        symbol=symbol,
        london_time=previous_day.replace(hour=0, minute=15),
        open_=midpoint,
        high=high,
        low=max(low + 0.02, low),
        close=midpoint,
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=previous_day.replace(hour=22, minute=45),
        open_=midpoint,
        high=min(high - 0.02, high),
        low=low,
        close=midpoint,
    )


def _seed_bullish_structure_context(db: Session, *, symbol: str, london_day: datetime) -> None:
    _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=0),
        open_=196.08,
        high=196.12,
        low=196.06,
        close=196.10,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=5),
        open_=196.10,
        high=196.14,
        low=196.03,
        close=196.06,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=10),
        open_=196.06,
        high=196.11,
        low=196.05,
        close=196.09,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=12),
        open_=196.01,
        high=196.06,
        low=195.94,
        close=196.02,
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=16),
        open_=196.08,
        high=196.20,
        low=196.07,
        close=196.18,
    )


def _seed_bearish_structure_context(db: Session, *, symbol: str, london_day: datetime) -> None:
    _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=0),
        open_=196.20,
        high=196.24,
        low=196.18,
        close=196.22,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=5),
        open_=196.22,
        high=196.28,
        low=196.17,
        close=196.26,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=10),
        open_=196.26,
        high=196.27,
        low=196.20,
        close=196.23,
        timeframe="M5",
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=12),
        open_=196.28,
        high=196.36,
        low=196.24,
        close=196.28,
    )
    _seed_candle(
        db,
        symbol=symbol,
        london_time=london_day.replace(hour=7, minute=16),
        open_=196.20,
        high=196.21,
        low=196.11,
        close=196.12,
    )


class SessionIntelTests(unittest.TestCase):
    def test_resolve_session_state_uses_configured_london_windows(self):
        config = get_symbol_market_config("GBPJPY")
        self.assertIsNotNone(config)

        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 6, 59, 59, tzinfo=LONDON_TZ), config=config),
            "asia",
        )
        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 7, 0, 0, tzinfo=LONDON_TZ), config=config),
            "london",
        )
        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 13, 30, 0, tzinfo=LONDON_TZ), config=config),
            "new_york",
        )
        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 17, 0, 0, tzinfo=LONDON_TZ), config=config),
            "new_york",
        )
        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 12, 0, 0, tzinfo=LONDON_TZ), config=config),
            "off_session",
        )
        self.assertEqual(
            resolve_session_state(now_london=datetime(2026, 1, 15, 17, 0, 1, tzinfo=LONDON_TZ), config=config),
            "off_session",
        )

    def test_dst_safe_asian_window_uses_london_day_boundaries(self):
        with test_db() as db:
            symbol = "GBPJPY"
            summer_day = datetime(2026, 7, 15, 0, 0, 0, tzinfo=LONDON_TZ)

            _seed_candle(
                db,
                symbol=symbol,
                london_time=summer_day.replace(hour=0, minute=15),
                open_=196.00,
                high=196.10,
                low=195.95,
                close=196.05,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=summer_day.replace(hour=6, minute=45),
                open_=196.06,
                high=196.22,
                low=196.01,
                close=196.20,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=summer_day.replace(hour=7, minute=5),
                open_=196.50,
                high=196.80,
                low=196.40,
                close=196.70,
            )
            db.commit()

            result = get_symbol_session_context(
                db,
                symbol=symbol,
                as_of_utc=summer_day.replace(hour=7, minute=30).astimezone(timezone.utc),
            )

            self.assertEqual(result["session_state"], "london")
            self.assertEqual(result["asian_range_window"]["start_utc"], "2026-07-14T23:00:00+00:00")
            self.assertEqual(result["asian_range_window"]["end_utc_exclusive"], "2026-07-15T06:00:00+00:00")
            self.assertEqual(result["asian_high"], 196.22)
            self.assertEqual(result["asian_low"], 195.95)

    def test_gbpjpy_pip_scaling_uses_0_01_pip_size(self):
        config = get_symbol_market_config("GBPJPY")
        self.assertIsNotNone(config)
        self.assertEqual(config.pip_size, 0.01)
        self.assertEqual(config.point_scale, 10)
        self.assertEqual(calculate_range_size_pips(high=196.35, low=196.05, config=config), 30.0)

    def test_anchor_selects_exact_london_0801_candle(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 15, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=0),
                open_=195.00,
                high=195.10,
                low=194.95,
                close=195.08,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.00,
                high=196.30,
                low=195.95,
                close=196.22,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=2),
                open_=197.00,
                high=197.05,
                low=196.90,
                close=196.95,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["anchor_available"])
            self.assertEqual(result["anchor_time_london"], "2026-01-15T08:01:00+00:00")
            self.assertEqual(result["anchor_time_utc"], "2026-01-15T08:01:00+00:00")
            self.assertEqual(result["anchor"]["open"], 196.0)
            self.assertEqual(result["anchor"]["high"], 196.3)
            self.assertEqual(result["anchor"]["low"], 195.95)
            self.assertEqual(result["anchor"]["close"], 196.22)

    def test_anchor_dst_safe_0801_maps_to_0701_utc_in_summer(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 7, 15, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.0,
                high=196.12,
                low=195.98,
                close=196.08,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["anchor_available"])
            self.assertEqual(result["anchor_time_london"], "2026-07-15T08:01:00+01:00")
            self.assertEqual(result["anchor_time_utc"], "2026-07-15T07:01:00+00:00")

    def test_anchor_classifies_bullish_acceptance(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 16, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.00,
                high=196.20,
                low=195.95,
                close=196.18,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertEqual(result["anchor_classification"], "bullish_acceptance")
            self.assertEqual(result["anchor_bias"], "bullish")
            self.assertEqual(result["anchor"]["direction"], "bullish")

    def test_anchor_classifies_bearish_acceptance(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 19, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.20,
                high=196.22,
                low=196.00,
                close=196.02,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertEqual(result["anchor_classification"], "bearish_acceptance")
            self.assertEqual(result["anchor_bias"], "bearish")
            self.assertEqual(result["anchor"]["direction"], "bearish")

    def test_anchor_classifies_bullish_rejection(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 20, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.20,
                high=196.23,
                low=195.95,
                close=196.22,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertEqual(result["anchor_classification"], "bullish_rejection")
            self.assertEqual(result["anchor_bias"], "bullish")

    def test_anchor_classifies_bearish_rejection(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 21, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.10,
                high=196.38,
                low=196.08,
                close=196.09,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertEqual(result["anchor_classification"], "bearish_rejection")
            self.assertEqual(result["anchor_bias"], "bearish")

    def test_anchor_neutral_fallback(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 22, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.10,
                high=196.18,
                low=196.02,
                close=196.11,
            )
            db.commit()

            result = _get_context_for_anchor(db, symbol=symbol, london_day=london_day)

            self.assertEqual(result["anchor_classification"], "neutral")
            self.assertEqual(result["anchor_bias"], "neutral")
            self.assertEqual(result["anchor_quality"], "weak")

    def test_detects_buyside_sweep_of_asian_high(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 23, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.28,
                high=196.36,
                low=196.26,
                close=196.35,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["sweep_available"])
            self.assertEqual(result["sweep_side"], "buy_side")
            self.assertEqual(result["swept_level"], 196.3)
            self.assertEqual(result["sweep_buffer_pips"], 6.0)

    def test_detects_sellside_sweep_of_asian_low(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 26, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.02,
                high=196.04,
                low=195.94,
                close=195.96,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["sweep_available"])
            self.assertEqual(result["sweep_side"], "sell_side")
            self.assertEqual(result["swept_level"], 196.0)
            self.assertEqual(result["sweep_buffer_pips"], 6.0)

    def test_classifies_rejection_sweep_when_price_closes_back_inside_range(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 27, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=22),
                open_=196.29,
                high=196.34,
                low=196.22,
                close=196.27,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["sweep_available"])
            self.assertEqual(result["sweep_type"], "rejection_sweep")
            self.assertTrue(result["returned_inside_range"])
            self.assertEqual(result["sweep_quality"], "moderate")

    def test_classifies_breakout_when_price_stays_outside_range_within_lookback(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 28, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.31,
                high=196.36,
                low=196.30,
                close=196.34,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=11),
                open_=196.34,
                high=196.37,
                low=196.32,
                close=196.35,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.35,
                high=196.39,
                low=196.33,
                close=196.38,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["sweep_available"])
            self.assertEqual(result["sweep_type"], "breakout")
            self.assertFalse(result["returned_inside_range"])
            self.assertEqual(result["sweep_quality"], "moderate")

    def test_classifies_double_sweep_when_both_asian_levels_are_taken(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 1, 29, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=8),
                open_=196.28,
                high=196.36,
                low=196.26,
                close=196.34,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=40),
                open_=196.02,
                high=196.04,
                low=195.93,
                close=196.01,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day)

            self.assertTrue(result["sweep_available"])
            self.assertEqual(result["sweep_side"], "both")
            self.assertEqual(result["sweep_type"], "double_sweep")
            self.assertEqual(result["sweep_quality"], "strong")
            self.assertIn("double_sweep_detected", result["sweep_notes"])

    def test_sweep_window_is_dst_safe_for_london_session(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 7, 15, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.28,
                high=196.34,
                low=196.25,
                close=196.31,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=30)

            self.assertEqual(result["session_state"], "london")
            self.assertEqual(result["london_session_window"]["start_utc"], "2026-07-15T06:00:00+00:00")
            self.assertEqual(result["london_session_window"]["end_utc_exclusive"], "2026-07-15T10:01:00+00:00")
            self.assertEqual(result["sweep_time_london"], "2026-07-15T07:05:00+01:00")
            self.assertEqual(result["sweep_time_utc"], "2026-07-15T06:05:00+00:00")

    def test_selects_next_buyside_liquidity_above_price(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 2, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_previous_day_range(db, symbol=symbol, london_day=london_day, high=196.05, low=195.85)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.06,
                high=196.08,
                low=196.04,
                close=196.08,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=15)

            self.assertEqual(result["next_buyside_liquidity"], 196.3)
            self.assertEqual(result["active_magnet_level"], 196.3)
            self.assertEqual(result["active_magnet_type"], "asian_high")
            self.assertEqual(result["magnet_bias"], "buyside")

    def test_selects_next_sellside_liquidity_below_price(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 3, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_previous_day_range(db, symbol=symbol, london_day=london_day, high=196.42, low=196.22)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=9),
                open_=196.23,
                high=196.24,
                low=196.23,
                close=196.24,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=15)

            self.assertEqual(result["next_sellside_liquidity"], 196.22)
            self.assertEqual(result["active_magnet_level"], 196.22)
            self.assertEqual(result["active_magnet_type"], "pdl")
            self.assertEqual(result["magnet_bias"], "sellside")

    def test_detects_round_number_target_when_it_is_next_ranked_liquidity(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 4, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_previous_day_range(db, symbol=symbol, london_day=london_day, high=196.4, low=196.1)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=6),
                open_=196.39,
                high=196.41,
                low=196.38,
                close=196.41,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=10)

            self.assertEqual(result["next_buyside_liquidity"], 196.5)

    def test_zone_state_classifies_premium(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 5, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=7),
                open_=196.22,
                high=196.24,
                low=196.21,
                close=196.24,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=10)

            self.assertEqual(result["zone_state"], "premium")
            self.assertEqual(result["equilibrium"], 196.15)
            self.assertEqual(result["distance_from_equilibrium_pips"], 9.0)

    def test_zone_state_classifies_discount(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 6, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=7),
                open_=196.04,
                high=196.06,
                low=196.03,
                close=196.06,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=10)

            self.assertEqual(result["zone_state"], "discount")

    def test_zone_state_classifies_equilibrium(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 9, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=7),
                open_=196.15,
                high=196.15,
                low=196.14,
                close=196.15,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=10)

            self.assertEqual(result["zone_state"], "equilibrium")
            self.assertEqual(result["distance_from_equilibrium_pips"], 0.0)

    def test_post_sweep_dealing_range_uses_sweep_extreme_and_opposite_asian_boundary(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 10, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.29,
                high=196.36,
                low=196.22,
                close=196.27,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertEqual(result["dealing_range_high"], 196.36)
            self.assertEqual(result["dealing_range_low"], 196.0)
            self.assertEqual(result["equilibrium"], 196.18)
            self.assertIn("post_sweep_uses_buyside_extreme_to_asian_low", result["zone_notes"])

    def test_detects_bullish_mss(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 11, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.14,
                low=196.03,
                close=196.06,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.06,
                high=196.11,
                low=196.05,
                close=196.09,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.01,
                high=196.06,
                low=195.94,
                close=196.02,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.08,
                high=196.20,
                low=196.07,
                close=196.18,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertTrue(result["structure_available"])
            self.assertEqual(result["structure_state"], "bullish_mss")
            self.assertEqual(result["structure_bias"], "bullish")
            self.assertTrue(result["mss_detected"])
            self.assertFalse(result["bos_detected"])
            self.assertEqual(result["break_level"], 196.14)
            self.assertEqual(result["break_time_london"], "2026-02-11T07:16:00+00:00")
            self.assertEqual(result["displacement_size_pips"], 4.0)

    def test_detects_bearish_mss(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 12, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.20,
                high=196.24,
                low=196.18,
                close=196.22,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.22,
                high=196.28,
                low=196.17,
                close=196.26,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.26,
                high=196.27,
                low=196.20,
                close=196.23,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.28,
                high=196.36,
                low=196.24,
                close=196.28,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.20,
                high=196.21,
                low=196.11,
                close=196.12,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertTrue(result["structure_available"])
            self.assertEqual(result["structure_state"], "bearish_mss")
            self.assertEqual(result["structure_bias"], "bearish")
            self.assertTrue(result["mss_detected"])
            self.assertFalse(result["bos_detected"])
            self.assertEqual(result["break_level"], 196.17)
            self.assertEqual(result["break_time_london"], "2026-02-12T07:16:00+00:00")
            self.assertEqual(result["displacement_size_pips"], 5.0)
            self.assertEqual(result["displacement_quality"], "moderate")

    def test_detects_bullish_bos(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 13, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.15,
                low=196.09,
                close=196.11,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.11,
                high=196.13,
                low=196.10,
                close=196.12,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.14,
                high=196.21,
                low=196.13,
                close=196.19,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertEqual(result["structure_state"], "bullish_bos")
            self.assertFalse(result["mss_detected"])
            self.assertTrue(result["bos_detected"])
            self.assertEqual(result["structure_bias"], "bullish")

    def test_detects_bearish_bos(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 16, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.18,
                high=196.20,
                low=196.14,
                close=196.16,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.16,
                high=196.18,
                low=196.13,
                close=196.14,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.14,
                high=196.19,
                low=196.15,
                close=196.16,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.12,
                high=196.13,
                low=196.08,
                close=196.09,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertEqual(result["structure_state"], "bearish_bos")
            self.assertFalse(result["mss_detected"])
            self.assertTrue(result["bos_detected"])
            self.assertEqual(result["structure_bias"], "bearish")

    def test_structure_falls_back_to_none_when_no_break_is_confirmed(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 17, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.15,
                low=196.09,
                close=196.11,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.11,
                high=196.13,
                low=196.10,
                close=196.12,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.12,
                high=196.14,
                low=196.11,
                close=196.14,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertTrue(result["structure_available"])
            self.assertEqual(result["structure_state"], "none")
            self.assertFalse(result["mss_detected"])
            self.assertFalse(result["bos_detected"])
            self.assertIsNone(result["break_level"])

    def test_structure_filters_breaks_below_displacement_threshold(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 18, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.15,
                low=196.09,
                close=196.11,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.11,
                high=196.13,
                low=196.10,
                close=196.12,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.14,
                high=196.18,
                low=196.13,
                close=196.17,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertEqual(result["structure_state"], "none")
            self.assertIsNone(result["break_level"])

    def test_structure_break_time_is_dst_safe_in_summer(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 7, 15, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.14,
                low=196.03,
                close=196.06,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.06,
                high=196.11,
                low=196.05,
                close=196.09,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.01,
                high=196.06,
                low=195.94,
                close=196.02,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.08,
                high=196.20,
                low=196.07,
                close=196.18,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertEqual(result["structure_state"], "bullish_mss")
            self.assertEqual(result["break_time_london"], "2026-07-15T07:16:00+01:00")
            self.assertEqual(result["break_time_utc"], "2026-07-15T06:16:00+00:00")

    def test_detects_bullish_fvg(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 19, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["fvg_available"])
            self.assertEqual(result["fvg_direction"], "bullish")
            self.assertEqual(result["fvg_state"], "fresh")
            self.assertEqual(result["fvg_low"], 196.2)
            self.assertEqual(result["fvg_high"], 196.26)
            self.assertEqual(result["fvg_mid"], 196.23)
            self.assertEqual(result["fvg_size_pips"], 6.0)
            self.assertEqual(result["fvg_created_time_london"], "2026-02-19T07:19:00+00:00")
            self.assertEqual(result["fvg_quality"], "moderate")

    def test_detects_bearish_fvg(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 20, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bearish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.12,
                high=196.14,
                low=196.10,
                close=196.11,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.10,
                high=196.12,
                low=196.07,
                close=196.08,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.03,
                high=196.05,
                low=196.00,
                close=196.01,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["fvg_available"])
            self.assertEqual(result["fvg_direction"], "bearish")
            self.assertEqual(result["fvg_state"], "fresh")
            self.assertEqual(result["fvg_low"], 196.05)
            self.assertEqual(result["fvg_high"], 196.1)
            self.assertEqual(result["fvg_size_pips"], 5.0)
            self.assertEqual(result["fvg_quality"], "moderate")

    def test_rejects_gap_that_is_too_small(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 23, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.20,
                high=196.21,
                low=196.19,
                close=196.20,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.22,
                high=196.23,
                low=196.22,
                close=196.23,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertFalse(result["fvg_available"])
            self.assertEqual(result["fvg_state"], "none")

    def test_rejects_expired_gap(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 24, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            for minute in range(20, 29):
                _seed_candle(
                    db,
                    symbol=symbol,
                    london_time=london_day.replace(hour=7, minute=minute),
                    open_=196.27,
                    high=196.28,
                    low=196.24,
                    close=196.26,
                )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=29)

            self.assertFalse(result["fvg_available"])
            self.assertEqual(result["fvg_state"], "none")

    def test_classifies_partially_mitigated_gap(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 25, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=20),
                open_=196.29,
                high=196.30,
                low=196.23,
                close=196.25,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["fvg_available"])
            self.assertEqual(result["fvg_state"], "partially_mitigated")
            self.assertTrue(result["fvg_mitigated"])

    def test_requires_fvg_to_form_after_structure_confirmation(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 26, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=13),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=14),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=15),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=0),
                open_=196.08,
                high=196.12,
                low=196.06,
                close=196.10,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=5),
                open_=196.10,
                high=196.14,
                low=196.03,
                close=196.06,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=10),
                open_=196.06,
                high=196.11,
                low=196.05,
                close=196.09,
                timeframe="M5",
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.01,
                high=196.06,
                low=195.94,
                close=196.02,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=16),
                open_=196.08,
                high=196.20,
                low=196.07,
                close=196.18,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["structure_available"])
            self.assertFalse(result["fvg_available"])
            self.assertEqual(result["fvg_state"], "none")

    def test_fvg_created_time_is_dst_safe_in_summer(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 7, 16, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["fvg_available"])
            self.assertEqual(result["fvg_created_time_london"], "2026-07-16T07:19:00+01:00")
            self.assertEqual(result["fvg_created_time_utc"], "2026-07-16T06:19:00+00:00")

    def test_evaluates_strong_bullish_ready_setup(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 2, 27, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.18,
                high=196.20,
                low=196.17,
                close=196.19,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.21,
                high=196.24,
                low=196.20,
                close=196.23,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.27,
                high=196.31,
                low=196.26,
                close=196.30,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["setup_available"])
            self.assertEqual(result["setup_direction"], "bullish")
            self.assertEqual(result["setup_state"], "ready")
            self.assertEqual(result["setup_confidence"], 70)
            self.assertEqual(result["setup_score"], 70)
            self.assertIn("bullish_mss", result["confirming_factors"])

    def test_evaluates_strong_bearish_ready_setup(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 3, 2, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bearish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=17),
                open_=196.12,
                high=196.14,
                low=196.10,
                close=196.11,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=18),
                open_=196.10,
                high=196.12,
                low=196.07,
                close=196.08,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=19),
                open_=196.03,
                high=196.05,
                low=196.00,
                close=196.01,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=21)

            self.assertTrue(result["setup_available"])
            self.assertEqual(result["setup_direction"], "bearish")
            self.assertEqual(result["setup_state"], "ready")
            self.assertEqual(result["setup_confidence"], 70)
            self.assertEqual(result["setup_score"], -70)
            self.assertIn("bearish_mss", result["confirming_factors"])

    def test_evaluates_developing_setup_without_fvg(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 3, 3, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertTrue(result["setup_available"])
            self.assertEqual(result["setup_direction"], "bullish")
            self.assertEqual(result["setup_state"], "developing")
            self.assertEqual(result["setup_confidence"], 50)
            self.assertFalse(result["fvg_available"])

    def test_evaluates_conflicted_setup_with_mixed_bias(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 3, 4, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_bullish_structure_context(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.34,
                high=196.36,
                low=196.26,
                close=196.28,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=8, as_of_minute=2)

            self.assertTrue(result["setup_available"])
            self.assertEqual(result["setup_state"], "conflicted")
            self.assertTrue(any(item.startswith("conflict:") for item in result["blocking_factors"]))

    def test_evaluates_invalid_setup_without_structure(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 3, 5, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_default_asian_range(db, symbol=symbol, london_day=london_day)
            _seed_candle(
                db,
                symbol=symbol,
                london_time=london_day.replace(hour=7, minute=12),
                open_=196.01,
                high=196.06,
                low=195.94,
                close=196.02,
            )
            db.commit()

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertFalse(result["setup_available"])
            self.assertEqual(result["setup_direction"], "bullish")
            self.assertEqual(result["setup_state"], "invalid")
            self.assertIn("missing_structure_confirmation", result["blocking_factors"])

    def test_evaluates_no_setup_fallback(self):
        with test_db() as db:
            symbol = "GBPJPY"
            london_day = datetime(2026, 3, 6, 0, 0, 0, tzinfo=LONDON_TZ)

            result = _get_context_for_sweep(db, symbol=symbol, london_day=london_day, as_of_hour=7, as_of_minute=20)

            self.assertFalse(result["setup_available"])
            self.assertIsNone(result["setup_direction"])
            self.assertEqual(result["setup_state"], "none")
            self.assertEqual(result["setup_confidence"], 0)
            self.assertEqual(result["setup_score"], 0)

    def test_get_symbol_session_context_computes_gbpjpy_asian_range(self):
        with test_db() as db:
            symbol = "GBPJPY"
            trade_day = datetime(2026, 1, 15, 0, 0, 0, tzinfo=LONDON_TZ)

            _seed_candle(
                db,
                symbol=symbol,
                london_time=trade_day.replace(hour=0, minute=5),
                open_=196.10,
                high=196.18,
                low=196.09,
                close=196.14,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=trade_day.replace(hour=3, minute=15),
                open_=196.16,
                high=196.35,
                low=196.12,
                close=196.31,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=trade_day.replace(hour=6, minute=59),
                open_=196.08,
                high=196.11,
                low=196.05,
                close=196.07,
            )
            _seed_candle(
                db,
                symbol=symbol,
                london_time=trade_day.replace(hour=7, minute=10),
                open_=198.00,
                high=199.00,
                low=197.50,
                close=198.50,
            )
            db.commit()

            result = get_symbol_session_context(
                db,
                symbol=symbol,
                as_of_utc=trade_day.replace(hour=8, minute=30).astimezone(timezone.utc),
            )

            self.assertEqual(result["symbol"], "GBPJPY")
            self.assertEqual(result["session_state"], "london")
            self.assertEqual(result["asian_high"], 196.35)
            self.assertEqual(result["asian_low"], 196.05)
            self.assertEqual(result["asian_mid"], 196.2)
            self.assertEqual(result["asian_range_size_pips"], 30.0)
            self.assertTrue(result["asian_range_valid"])
            self.assertEqual(result["source_timeframe_used"], "M1")

    def test_route_returns_session_context_for_selected_gbpjpy(self):
        with test_db() as db:
            london_day = datetime(2026, 1, 15, 0, 0, 0, tzinfo=LONDON_TZ)
            _seed_candle(
                db,
                symbol="GBPJPY",
                london_time=london_day.replace(hour=8, minute=1),
                open_=196.0,
                high=196.15,
                low=195.98,
                close=196.10,
            )
            user = User(
                full_name="Admin",
                email="admin@example.com",
                password_hash="x",
                role="admin",
                is_active=True,
            )
            db.add(user)
            db.flush()
            db.add(UserSignalPref(user_id=user.id, symbols_json=["GBPJPY"]))
            db.commit()

            result = get_oracle_session_context(
                symbol="GBPJPY",
                as_of_utc=datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc),
                user=user,
                db=db,
            )

            self.assertEqual(result["symbol"], "GBPJPY")
            self.assertEqual(result["session_state"], "london")
            self.assertIn("london_now", result)
            self.assertEqual(result["source_timeframe_used"], "M1")
            self.assertTrue(result["anchor_available"])
            self.assertIn("anchor", result)


if __name__ == "__main__":
    unittest.main()
