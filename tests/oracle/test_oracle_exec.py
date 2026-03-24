from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.models import MT5Candle, OracleRun
from app.services.oracle_exec import build_execution_instruction


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


class OracleExecTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_allow_off_session = settings.ORACLE_EXEC_ALLOW_OFF_SESSION
        settings.ORACLE_EXEC_ALLOW_OFF_SESSION = True

    def tearDown(self) -> None:
        settings.ORACLE_EXEC_ALLOW_OFF_SESSION = self._orig_allow_off_session

    def _seed_market_and_run(
        self,
        db: Session,
        *,
        symbol: str = "XAUUSD",
        quarterly_bias: str = "BUY_ONLY",
        daily_bias: str = "BUY_ONLY",
        final_allowed_soft: str = "BUY_ONLY",
        confirm_ok: bool = True,
        alignment: str = "ALIGNED",
    ) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="H1",
                time_utc=now - timedelta(minutes=45),
                open=2310.0,
                high=2313.5,
                low=2309.2,
                close=2312.4,
                volume=1200,
            )
        )
        db.add(
            MT5Candle(
                symbol=symbol,
                timeframe="M15",
                time_utc=now - timedelta(minutes=15),
                open=2311.8,
                high=2312.9,
                low=2311.3,
                close=2312.5,
                volume=680,
            )
        )
        db.add(
            OracleRun(
                id=uuid.uuid4(),
                symbol=symbol,
                timeframe="H1",
                as_of_utc=now - timedelta(minutes=15),
                bias=daily_bias,
                confidence=0.74,
                manipulation_score=10,
                manipulation_level="low",
                internal_json={},
                public_json={
                    "quarterly_bias": quarterly_bias,
                    "daily_bias_raw": daily_bias,
                    "allowed_direction_final_soft": final_allowed_soft,
                    "confirm_ok": confirm_ok,
                    "volume_state": "normal",
                    "atr_h1": 1.7,
                    "permission_alignment": alignment,
                },
                status="confirmed",
            )
        )
        db.commit()

    def test_exec_enabled_for_elite_when_all_gates_pass(self):
        with test_db() as db:
            self._seed_market_and_run(db)
            result = build_execution_instruction(db, symbol="XAUUSD", target_tier="elite", requested_session="auto")
            self.assertTrue(result.get("enabled"))
            self.assertEqual(result.get("symbol"), "XAUUSD")
            self.assertIn(result.get("side"), {"BUY", "SELL"})
            self.assertIsNotNone((result.get("entry_zone") or {}).get("min"))
            self.assertEqual((result.get("meta") or {}).get("reasons"), [])

    def test_exec_disabled_for_non_elite_target_tier(self):
        with test_db() as db:
            self._seed_market_and_run(db)
            result = build_execution_instruction(db, symbol="XAUUSD", target_tier="basic", requested_session="auto")
            self.assertFalse(result.get("enabled"))
            reasons = (result.get("meta") or {}).get("reasons", [])
            self.assertIn("elite_only", reasons)

    def test_exec_disabled_on_quarterly_conflict(self):
        with test_db() as db:
            self._seed_market_and_run(
                db,
                quarterly_bias="SELL_ONLY",
                daily_bias="BUY_ONLY",
                final_allowed_soft="BUY_ONLY",
                confirm_ok=True,
                alignment="CONFLICT",
            )
            result = build_execution_instruction(db, symbol="XAUUSD", target_tier="elite", requested_session="auto")
            self.assertFalse(result.get("enabled"))
            reasons = (result.get("meta") or {}).get("reasons", [])
            self.assertIn("quarterly_permission_block", reasons)
            self.assertIn("permission_conflict", reasons)


if __name__ == "__main__":
    unittest.main()
