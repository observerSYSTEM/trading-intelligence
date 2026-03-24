from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.ops import _resolve_anchor_final_allowed
from app.db.base import Base
from app.db.models import DailyPermissionSnapshot, GoldRegimeDaily


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


class OpsAnchorDebugTests(unittest.TestCase):
    def test_final_allowed_matches_same_permission_snapshot_not_newer_unrelated_regime(self):
        with test_db() as db:
            permission_as_of = datetime(2026, 3, 21, 8, 1, tzinfo=timezone.utc)
            permission_row = DailyPermissionSnapshot(
                symbol="XAUUSD",
                date_uk=date(2026, 3, 21),
                for_date=date(2026, 3, 21),
                timeframe="M1",
                as_of_utc=permission_as_of,
                computed_at_utc=datetime(2026, 3, 21, 16, 2, tzinfo=timezone.utc),
                daily_permission="NO_TRADE",
                daily_permission_stage="OFFICIAL",
                permission_source="LONDON_0801",
                official=True,
                factors_json={},
            )
            db.add(permission_row)

            db.add(
                GoldRegimeDaily(
                    symbol="XAUUSD",
                    as_of_utc=datetime(2026, 3, 24, 10, 45, tzinfo=timezone.utc),
                    regime="bullish",
                    confidence=0.74,
                    allowed_direction="BUY_ONLY",
                    final_allowed_basic="BUY_ONLY",
                    final_allowed_elite="BUY_ONLY",
                    daily_bias="bullish",
                    confirm_ok=True,
                    public_factors_json={
                        "daily_permission_as_of_utc": datetime(2026, 3, 24, 8, 1, tzinfo=timezone.utc).isoformat(),
                        "permission_for_date_uk": "2026-03-24",
                    },
                    internal_factors_json={},
                )
            )
            db.add(
                GoldRegimeDaily(
                    symbol="XAUUSD",
                    as_of_utc=datetime(2026, 3, 21, 10, 45, tzinfo=timezone.utc),
                    regime="neutral",
                    confidence=0.28,
                    allowed_direction="NO_TRADE",
                    final_allowed_basic="NO_TRADE",
                    final_allowed_elite="NO_TRADE",
                    daily_bias="neutral",
                    confirm_ok=False,
                    public_factors_json={
                        "daily_permission_as_of_utc": permission_as_of.isoformat(),
                        "permission_for_date_uk": "2026-03-21",
                    },
                    internal_factors_json={},
                )
            )
            db.commit()

            resolved = _resolve_anchor_final_allowed(db, symbol="XAUUSD", permission_row=permission_row)

            self.assertEqual(resolved, "NO_TRADE")

    def test_final_allowed_falls_back_to_permission_when_no_matching_regime_exists(self):
        with test_db() as db:
            permission_row = DailyPermissionSnapshot(
                symbol="XAUUSD",
                date_uk=date(2026, 3, 24),
                for_date=date(2026, 3, 24),
                timeframe="M1",
                as_of_utc=datetime(2026, 3, 24, 8, 1, tzinfo=timezone.utc),
                computed_at_utc=datetime(2026, 3, 24, 8, 2, tzinfo=timezone.utc),
                daily_permission="SELL_ONLY",
                daily_permission_stage="OFFICIAL",
                permission_source="LONDON_0801",
                official=True,
                factors_json={},
            )
            db.add(permission_row)
            db.commit()

            resolved = _resolve_anchor_final_allowed(db, symbol="XAUUSD", permission_row=permission_row)

            self.assertEqual(resolved, "SELL_ONLY")


if __name__ == "__main__":
    unittest.main()
