from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    AutoTradeGlobalControl,
    OracleRun,
    Subscription,
    TradeJob,
    User,
    UserRiskSetting,
    UserSymbolPreference,
)
from app.services.autotrade_service import next_trade_job_for_runner, queue_autotrade_job_for_user


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


def _seed_user(db: Session) -> User:
    user = User(
        id=uuid.uuid4(),
        email="admin@test.com",
        password_hash="hash",
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        Subscription(
            user_id=user.id,
            plan="elite",
            status="active",
            autotrade_enabled=True,
        )
    )
    db.add(
        UserSymbolPreference(
            user_id=user.id,
            symbol="XAUUSD",
            enabled=True,
            autotrade_enabled=True,
        )
    )
    db.add(
        OracleRun(
            id=uuid.uuid4(),
            symbol="XAUUSD",
            timeframe="H1",
            as_of_utc=datetime(2026, 2, 16, 0, 0, tzinfo=timezone.utc),
            bias="BUY_ONLY",
            confidence=0.8,
            manipulation_score=0,
            manipulation_level="low",
            internal_json={},
            public_json={
                "confirm_ok": True,
                "confirm_tf": "M15",
                "allowed_direction_final_soft": "BUY_ONLY",
                "final_allowed_elite": "BUY_ONLY",
                "news_gate_pass": True,
                "risk_banner": {"is_blueprint_day": False, "volume_spike": False},
            },
            status="confirmed",
        )
    )
    db.commit()
    return user


class AutoTradeServiceTests(unittest.TestCase):
    def setUp(self):
        self._old_autotrade_enabled = settings.AUTOTRADE_ENABLED
        self._old_autotrade_admin_email = settings.AUTOTRADE_ADMIN_EMAIL
        settings.AUTOTRADE_ENABLED = True
        settings.AUTOTRADE_ADMIN_EMAIL = "admin@test.com"

    def tearDown(self):
        settings.AUTOTRADE_ENABLED = self._old_autotrade_enabled
        settings.AUTOTRADE_ADMIN_EMAIL = self._old_autotrade_admin_email

    def test_queue_blocked_when_global_killswitch_off(self):
        with test_db() as db:
            user = _seed_user(db)
            db.add(AutoTradeGlobalControl(id=1, autotrade_enabled=False))
            db.commit()

            result = queue_autotrade_job_for_user(db, user_id=user.id, symbol="XAUUSD")
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "global_kill_switch")
            event = db.query(AuditEvent).filter(AuditEvent.user_id == user.id).order_by(AuditEvent.created_at.desc()).first()
            self.assertIsNotNone(event)
            assert event is not None
            self.assertFalse(event.allowed)
            self.assertEqual(event.action, "autotrade.queue_precheck")

    def test_queue_and_dispatch_success(self):
        with test_db() as db:
            user = _seed_user(db)
            db.add(AutoTradeGlobalControl(id=1, autotrade_enabled=True))
            db.commit()

            with patch(
                "app.services.autotrade_service.build_execution_instruction",
                return_value={
                    "enabled": True,
                    "side": "BUY",
                    "entry_zone": {"order_type": "MARKET"},
                    "sl": 2300.0,
                    "tp1": 2350.0,
                    "tp2": 2375.0,
                    "expires_at_utc": "2099-12-31T01:00:00+00:00",
                },
            ):
                queued = queue_autotrade_job_for_user(db, user_id=user.id, symbol="XAUUSD")
            self.assertTrue(queued["ok"])
            self.assertEqual(queued["reason"], "queued")

            payload = next_trade_job_for_runner(db, runner_id="r1")
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["symbol"], "XAUUSD")
            self.assertEqual(payload["side"], "BUY")

    def test_blueprint_multiplier_applies_to_volume(self):
        with test_db() as db:
            user = _seed_user(db)
            db.add(AutoTradeGlobalControl(id=1, autotrade_enabled=True))
            run = db.query(OracleRun).filter(OracleRun.symbol == "XAUUSD").first()
            assert run is not None
            run.public_json = {
                **(run.public_json or {}),
                "confirm_ok": True,
                "confirm_tf": "M15",
                "allowed_direction_final_soft": "BUY_ONLY",
                "final_allowed_elite": "BUY_ONLY",
                "news_gate_pass": True,
                "risk_banner": {
                    "is_blueprint_day": True,
                    "volume_spike": False,
                    "suggested_risk_multiplier": 0.5,
                },
            }
            db.add(run)
            db.add(
                UserRiskSetting(
                    user_id=user.id,
                    risk_mode="fixed",
                    risk_value=0.10,
                    max_lot=0.10,
                    max_trades_day=3,
                    max_daily_loss=3.0,
                    max_open_trades=1,
                    allowed_symbols_json=[],
                    avoid_mondays=False,
                    block_on_volume_spike=False,
                    news_filter_enabled=True,
                    news_block_minutes=30,
                )
            )
            db.commit()

            with patch(
                "app.services.autotrade_service.build_execution_instruction",
                return_value={
                    "enabled": True,
                    "side": "BUY",
                    "entry_zone": {"order_type": "MARKET"},
                    "sl": 2300.0,
                    "tp1": 2350.0,
                    "tp2": 2375.0,
                    "expires_at_utc": "2099-12-31T01:00:00+00:00",
                },
            ):
                queued = queue_autotrade_job_for_user(db, user_id=user.id, symbol="XAUUSD")
            self.assertTrue(queued["ok"])
            self.assertAlmostEqual(float(queued["volume"]), 0.05, places=6)
            job = db.query(TradeJob).filter(TradeJob.user_id == user.id).order_by(TradeJob.created_at.desc()).first()
            self.assertIsNotNone(job)
            assert job is not None
            self.assertAlmostEqual(float(job.volume), 0.05, places=6)

    def test_volume_spike_block_when_user_prefers_block(self):
        with test_db() as db:
            user = _seed_user(db)
            db.add(AutoTradeGlobalControl(id=1, autotrade_enabled=True))
            run = db.query(OracleRun).filter(OracleRun.symbol == "XAUUSD").first()
            assert run is not None
            run.public_json = {
                **(run.public_json or {}),
                "confirm_ok": True,
                "confirm_tf": "M15",
                "allowed_direction_final_soft": "BUY_ONLY",
                "final_allowed_elite": "BUY_ONLY",
                "news_gate_pass": True,
                "risk_banner": {
                    "is_blueprint_day": False,
                    "volume_spike": True,
                    "suggested_risk_multiplier": 0.25,
                },
            }
            db.add(run)
            db.add(
                UserRiskSetting(
                    user_id=user.id,
                    risk_mode="fixed",
                    risk_value=0.10,
                    max_lot=0.10,
                    max_trades_day=3,
                    max_daily_loss=3.0,
                    max_open_trades=1,
                    allowed_symbols_json=[],
                    avoid_mondays=False,
                    block_on_volume_spike=True,
                    news_filter_enabled=True,
                    news_block_minutes=30,
                )
            )
            db.commit()

            result = queue_autotrade_job_for_user(db, user_id=user.id, symbol="XAUUSD")
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "risk_gate_blocked")
            self.assertIn("volume_spike_blocked", result.get("risk_reasons", []))

    def test_dispatch_blocks_if_user_switch_turned_off_after_queue(self):
        with test_db() as db:
            user = _seed_user(db)
            db.add(AutoTradeGlobalControl(id=1, autotrade_enabled=True))
            db.commit()

            with patch(
                "app.services.autotrade_service.build_execution_instruction",
                return_value={
                    "enabled": True,
                    "side": "BUY",
                    "entry_zone": {"order_type": "MARKET"},
                    "sl": 2300.0,
                    "tp1": 2350.0,
                    "tp2": 2375.0,
                    "expires_at_utc": "2099-12-31T01:00:00+00:00",
                },
            ):
                queued = queue_autotrade_job_for_user(db, user_id=user.id, symbol="XAUUSD")
            self.assertTrue(queued["ok"])

            sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
            assert sub is not None
            sub.autotrade_enabled = False
            db.add(sub)
            db.commit()

            payload = next_trade_job_for_runner(db, runner_id="r1")
            self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
