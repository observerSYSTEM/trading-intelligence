from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import NotificationRoute, Subscription, User
from app.services.admin_oracle_automation import run_oracle_and_send
from app.services.usage_service import consume_usage, get_usage


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


def _seed_user(db: Session, *, email: str, plan: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(
        Subscription(
            user_id=user.id,
            plan=plan,
            status="active",
        )
    )
    db.add(
        NotificationRoute(
            user_id=user.id,
            telegram_enabled=True,
            telegram_chat_id=f"chat-{email}",
        )
    )
    db.commit()
    return user


def _snapshot(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "as_of": None,
        "final_allowed_basic": "BUY_ONLY",
        "final_allowed_elite": "BUY_ONLY",
        "confidence": 0.7,
        "next_liquidity_magnet": 123.4,
        "news_gate_pass": True,
        "volume_state": "normal",
        "internal": {"confirmation": {"manipulation_level": "low"}},
        "risk_banner": {"tier_copy": {"basic": "basic", "pro": "pro", "elite": "elite"}},
        "weekly_range": {"range_ready": False},
    }


class AdminOracleRunAndSendTests(unittest.TestCase):
    def test_filters_by_tier_and_symbol_and_consumes_non_elite_only(self):
        with test_db() as db:
            basic = _seed_user(db, email="basic@test.com", plan="basic")
            pro = _seed_user(db, email="pro@test.com", plan="pro")
            elite = _seed_user(db, email="elite@test.com", plan="elite")

            with patch("app.services.admin_oracle_automation.compute_dual_timeframe_snapshot", return_value=_snapshot("EURUSD")), patch(
                "app.services.admin_oracle_automation.send_thread_update",
                return_value={"anchor_message_id": 1, "message_id": 2},
            ) as send_mock:
                result = run_oracle_and_send(
                    db,
                    symbols=["EURUSD"],
                    tier_min="basic",
                    mode="daily_bias",
                    dry_run=False,
                    admin_user_id=uuid.uuid4(),
                )

            self.assertEqual(result["sent_count"], 2)  # pro + elite
            self.assertGreaterEqual(result["skipped_reasons"].get("symbol_not_enabled", 0), 1)  # basic
            self.assertEqual(send_mock.call_count, 2)

            usage_basic = get_usage(db, basic.id)
            usage_pro = get_usage(db, pro.id)
            usage_elite = get_usage(db, elite.id)
            self.assertEqual(usage_basic["used"], 0)
            self.assertEqual(usage_pro["used"], 1)
            self.assertEqual(usage_elite["used"], 0)

    def test_does_not_consume_usage_when_send_fails(self):
        with test_db() as db:
            pro = _seed_user(db, email="pro-fail@test.com", plan="pro")

            with patch("app.services.admin_oracle_automation.compute_dual_timeframe_snapshot", return_value=_snapshot("XAUUSD")), patch(
                "app.services.admin_oracle_automation.send_thread_update",
                side_effect=RuntimeError("telegram down"),
            ):
                result = run_oracle_and_send(
                    db,
                    symbols=["XAUUSD"],
                    tier_min="basic",
                    mode="daily_bias",
                    dry_run=False,
                    admin_user_id=uuid.uuid4(),
                )

            self.assertEqual(result["sent_count"], 0)
            self.assertEqual(result["blocked_reasons"].get("telegram_send_failed"), 1)
            usage_pro = get_usage(db, pro.id)
            self.assertEqual(usage_pro["used"], 0)

    def test_skips_when_usage_limit_exceeded(self):
        with test_db() as db:
            basic = _seed_user(db, email="basic-limit@test.com", plan="basic")
            for i in range(30):
                consume_usage(
                    db,
                    basic.id,
                    n=1,
                    reason="seed",
                    symbol="XAUUSD",
                    signal_id=f"seed-{i}",
                )
            db.commit()

            with patch("app.services.admin_oracle_automation.compute_dual_timeframe_snapshot", return_value=_snapshot("XAUUSD")), patch(
                "app.services.admin_oracle_automation.send_thread_update"
            ) as send_mock:
                result = run_oracle_and_send(
                    db,
                    symbols=["XAUUSD"],
                    tier_min="basic",
                    mode="daily_bias",
                    dry_run=False,
                    admin_user_id=uuid.uuid4(),
                )

            self.assertEqual(result["sent_count"], 0)
            self.assertEqual(result["skipped_reasons"].get("usage_limit_exceeded"), 1)
            send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()

