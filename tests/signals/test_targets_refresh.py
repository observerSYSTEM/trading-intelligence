from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import MT5Candle, MT5IngestStatus, OracleTargetsSnapshot
from app.services.targets_refresh import (
    detect_magnet_hit,
    latest_market_feed_freshness,
    maybe_refresh_targets_on_magnet_hit,
    refresh_targets_for_all_symbols,
    recompute_targets_snapshot,
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


def _seed_h1_history(db: Session, symbol: str, *, now_utc: datetime) -> None:
    start = now_utc - timedelta(hours=30)
    for idx in range(30):
        t = start + timedelta(hours=idx)
        base = 100.0 + (idx * 0.15)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="H1",
                time_utc=t,
                open=base,
                high=base + 1.2,
                low=base - 1.0,
                close=base + 0.4,
                volume=1000 + (idx * 5),
            )
        )


def _seed_m1_price(db: Session, symbol: str, *, now_utc: datetime, close: float) -> None:
    db.add(
        MT5Candle(
            symbol=symbol,
            timeframe="M1",
            time_utc=now_utc,
            open=close - 0.05,
            high=close + 0.05,
            low=close - 0.08,
            close=close,
            volume=200,
        )
    )


class TargetsRefreshTests(unittest.TestCase):
    def test_latest_market_feed_freshness_prefers_newer_ingest_timestamp(self):
        with test_db() as db:
            symbol = "XAUUSD"
            candle_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
            ingest_time = candle_time + timedelta(minutes=3)
            now_utc = ingest_time + timedelta(minutes=1)

            db.add(
                MT5Candle(
                    symbol=symbol,
                    timeframe="M15",
                    time_utc=candle_time,
                    open=2300.0,
                    high=2301.0,
                    low=2299.5,
                    close=2300.5,
                    volume=1000.0,
                )
            )
            db.add(MT5IngestStatus(symbol=symbol, last_ingested_at=ingest_time))
            db.commit()

            with patch("app.services.targets_refresh.api_candle_mode", return_value=False):
                freshness = latest_market_feed_freshness(db, symbol=symbol, now_utc=now_utc)

            self.assertEqual(freshness["latest_market_feed_source"], "ingest_status")
            self.assertEqual(freshness["latest_market_feed_at"], ingest_time)
            self.assertEqual(freshness["market_feed_age_seconds"], 60)
            self.assertFalse(freshness["market_feed_delayed"])

    def test_api_market_feed_freshness_uses_ingest_status_updated_at(self):
        with test_db() as db:
            symbol = "XAUUSD"
            candle_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
            ingest_updated_at = candle_time + timedelta(minutes=14)
            now_utc = ingest_updated_at + timedelta(minutes=1)

            db.add(
                MT5Candle(
                    symbol=symbol,
                    timeframe="M15",
                    time_utc=candle_time,
                    open=2300.0,
                    high=2301.0,
                    low=2299.5,
                    close=2300.5,
                    volume=1000.0,
                )
            )
            db.add(
                MT5IngestStatus(
                    symbol=symbol,
                    last_ingested_at=candle_time,
                    updated_at=ingest_updated_at,
                )
            )
            db.commit()

            with patch("app.services.targets_refresh.api_candle_mode", return_value=True):
                freshness = latest_market_feed_freshness(db, symbol=symbol, now_utc=now_utc)

            self.assertEqual(freshness["latest_market_feed_source"], "ingest_status_updated_at")
            self.assertEqual(freshness["latest_market_feed_at"], ingest_updated_at)
            self.assertEqual(freshness["market_feed_age_seconds"], 60)
            self.assertFalse(freshness["market_feed_delayed"])

    def test_latest_market_feed_freshness_falls_back_to_latest_candle(self):
        with test_db() as db:
            symbol = "EURUSD"
            candle_time = datetime(2026, 5, 1, 9, 58, tzinfo=timezone.utc)
            now_utc = candle_time + timedelta(minutes=1)

            db.add(
                MT5Candle(
                    symbol=symbol,
                    timeframe="M1",
                    time_utc=candle_time,
                    open=1.08,
                    high=1.081,
                    low=1.0795,
                    close=1.0805,
                    volume=500.0,
                )
            )
            db.commit()

            with patch("app.services.targets_refresh.api_candle_mode", return_value=False):
                freshness = latest_market_feed_freshness(db, symbol=symbol, now_utc=now_utc)

            self.assertEqual(freshness["latest_market_feed_source"], "latest_candle:M1")
            self.assertEqual(freshness["latest_market_feed_at"], candle_time)
            self.assertEqual(freshness["latest_candle_timeframe"], "M1")
            self.assertFalse(freshness["market_feed_delayed"])

    def test_refresh_targets_for_all_symbols_uses_explicit_as_of_timestamp(self):
        with test_db() as db:
            symbol = "GBPJPY"
            history_now = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
            refresh_as_of = history_now + timedelta(minutes=4)
            _seed_h1_history(db, symbol, now_utc=history_now)
            _seed_m1_price(db, symbol, now_utc=history_now, close=195.25)
            db.commit()

            rows = refresh_targets_for_all_symbols(
                db,
                symbols=[symbol],
                reason="manual_run",
                tiers=["pro"],
                as_of_utc=refresh_as_of,
            )
            db.commit()

            self.assertEqual(rows[0]["as_of_utc"], refresh_as_of.isoformat())
            latest = (
                db.query(OracleTargetsSnapshot)
                .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == "pro")
                .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
                .first()
            )
            self.assertIsNotNone(latest)
            self.assertEqual(latest.as_of_utc.replace(tzinfo=timezone.utc), refresh_as_of)

    def test_detect_magnet_hit_buy_and_sell(self):
        buy_hit = detect_magnet_hit(
            magnet_side="BUY",
            magnet_price=100.0,
            bid=99.8,
            ask=99.95,
            atr_h1=2.0,
            m1_close=100.1,
        )
        self.assertTrue(buy_hit["hit"])
        self.assertEqual(buy_hit["confidence"], "confirmed")

        sell_hit = detect_magnet_hit(
            magnet_side="SELL",
            magnet_price=100.0,
            bid=100.02,
            ask=100.12,
            atr_h1=2.0,
            m1_close=99.9,
        )
        self.assertTrue(sell_hit["hit"])
        self.assertEqual(sell_hit["confidence"], "confirmed")

    def test_magnet_hit_inserts_new_snapshot_with_updated_magnet(self):
        with test_db() as db:
            symbol = "XAUUSD"
            now_utc = datetime(2026, 2, 16, 12, 0, tzinfo=timezone.utc)
            _seed_h1_history(db, symbol, now_utc=now_utc)
            _seed_m1_price(db, symbol, now_utc=now_utc, close=104.0)
            db.commit()

            first = recompute_targets_snapshot(
                db,
                symbol=symbol,
                tier="pro",
                price_bid=104.0,
                price_ask=104.1,
                as_of_utc=now_utc,
                reason="test_seed",
            )
            db.commit()

            first_state = first.magnet_state if isinstance(first.magnet_state, dict) else {}
            current = first_state.get("current") if isinstance(first_state.get("current"), dict) else {}
            current_side = str(current.get("side") or "BUY").upper()
            current_price = float(current.get("price") or first.magnet_price)

            if current_side == "BUY":
                bid = current_price - 0.08
                ask = current_price - 0.01
                m1_close = current_price + 0.01
            else:
                bid = current_price + 0.01
                ask = current_price + 0.08
                m1_close = current_price - 0.01

            with patch(
                "app.services.targets_refresh.maybe_send_liquidity_target_alert",
                return_value={"status": "alert_sent"},
            ) as alert_mock:
                result = maybe_refresh_targets_on_magnet_hit(
                    db,
                    symbol=symbol,
                    bid=bid,
                    ask=ask,
                    m1_close=m1_close,
                    event_time_utc=now_utc + timedelta(seconds=1),
                    tier="pro",
                )
            db.commit()
            self.assertTrue(result["hit"])
            alert_mock.assert_called_once()

            rows = (
                db.query(OracleTargetsSnapshot)
                .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == "pro")
                .order_by(OracleTargetsSnapshot.as_of_utc.asc(), OracleTargetsSnapshot.created_at.asc())
                .all()
            )
            self.assertGreaterEqual(len(rows), 2)
            latest = rows[-1]
            self.assertNotEqual(str(first.id), str(latest.id))
            self.assertGreater(latest.as_of_utc, first.as_of_utc)

            latest_state = latest.magnet_state if isinstance(latest.magnet_state, dict) else {}
            self.assertIsNotNone(latest_state.get("hit"))
            new_current = latest_state.get("current") if isinstance(latest_state.get("current"), dict) else {}
            self.assertNotEqual(str(new_current.get("side") or ""), current_side)


if __name__ == "__main__":
    unittest.main()
