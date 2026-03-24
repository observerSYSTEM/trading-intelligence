from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.schemas.signal import SignalCreate
from app.services.signal_service import create_signal, extract_signal_fields


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


class SignalServiceTests(unittest.TestCase):
    def test_active_setup_refresh_updates_same_row_and_preserves_valid_magnet(self):
        with test_db() as db:
            detected_at = datetime(2026, 3, 21, 10, 15, tzinfo=timezone.utc)
            first = SignalCreate(
                symbol="XAUUSD",
                timeframe="M15",
                signal_type="opportunity_m15_confirmed",
                direction="SELL_ONLY",
                bias="SELL_ONLY",
                magnet=3334.25,
                price=3331.10,
                reason="Opportunity aligned with daily permission and H1 confirmation.",
                confidence=0.84,
                daily_permission="SELL_ONLY",
                h1_confirmation="CONFIRMED",
                zone_target=3321.50,
                sellside_liquidity=3334.25,
                buyside_liquidity=3366.80,
                source="oracle_engine",
                detected_at=detected_at,
                meta={
                    "active_setup_key": "XAUUSD|M15|opportunity_m15_confirmed|SELL_ONLY|SELL_ONLY|CONFIRMED|2026-03-21",
                    "daily_alignment": "ALIGNED",
                },
            )

            first_row, first_duplicate = create_signal(db, payload=first)
            db.commit()

            self.assertFalse(first_duplicate)
            self.assertIsNotNone(first_row.id)
            self.assertEqual(first_row.magnet_level, 3334.25)

            refreshed = SignalCreate(
                symbol="XAUUSD",
                timeframe="M15",
                signal_type="opportunity_m15_confirmed",
                direction="SELL_ONLY",
                bias="SELL_ONLY",
                magnet=None,
                price=3330.55,
                reason="Opportunity aligned with daily permission and H1 confirmation.",
                confidence=0.87,
                daily_permission="SELL_ONLY",
                h1_confirmation="CONFIRMED",
                zone_target=3318.20,
                sellside_liquidity=3334.25,
                buyside_liquidity=3364.40,
                source="oracle_engine",
                detected_at=detected_at + timedelta(minutes=15),
                meta={
                    "active_setup_key": "XAUUSD|M15|opportunity_m15_confirmed|SELL_ONLY|SELL_ONLY|CONFIRMED|2026-03-21",
                    "daily_alignment": "ALIGNED",
                },
            )

            refreshed_row, refreshed_duplicate = create_signal(db, payload=refreshed)
            db.commit()

            self.assertFalse(refreshed_duplicate)
            self.assertEqual(str(first_row.id), str(refreshed_row.id))
            self.assertEqual(refreshed_row.magnet_level, 3334.25)
            self.assertEqual(
                refreshed_row.detected_at.replace(tzinfo=timezone.utc),
                detected_at + timedelta(minutes=15),
            )

            details = extract_signal_fields(refreshed_row)
            self.assertEqual(details["magnet"], 3334.25)
            self.assertEqual(details["zone_target"], 3318.20)
            self.assertEqual(details["sellside_liquidity"], 3334.25)
            self.assertEqual(details["buyside_liquidity"], 3364.40)
            self.assertEqual(details["daily_permission"], "SELL_ONLY")
            self.assertEqual(details["h1_confirmation"], "CONFIRMED")


if __name__ == "__main__":
    unittest.main()
