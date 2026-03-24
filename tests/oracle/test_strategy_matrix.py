from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import DeliveryLog, NotificationRoute, Subscription, User
from app.services.oracle_scheduler import _send_thread_message_with_quota
from app.services.strategy_matrix import StrategyMatrixError, validate_symbol_for_strategy


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


def _seed_user(db: Session, *, plan: str = "elite") -> tuple[User, Subscription, NotificationRoute]:
    user = User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4()}@test.com",
        password_hash="hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()

    sub = Subscription(
        user_id=user.id,
        plan=plan,
        status="active",
    )
    route = NotificationRoute(
        user_id=user.id,
        telegram_enabled=True,
        telegram_chat_id="123456789",
    )
    db.add(sub)
    db.add(route)
    db.commit()
    return user, sub, route


class StrategyMatrixTests(unittest.TestCase):
    def test_validate_symbol_for_strategy_blocks_unsupported_symbol(self):
        with self.assertRaises(StrategyMatrixError) as ctx:
            validate_symbol_for_strategy(symbol="GBPUSD", strategy_name="VOL_MANIP", tier="elite")
        self.assertEqual(ctx.exception.reason, "symbol_not_supported")

    def test_validate_symbol_for_strategy_blocks_non_elite_news_exec(self):
        with self.assertRaises(StrategyMatrixError) as ctx:
            validate_symbol_for_strategy(symbol="XAUUSD", strategy_name="NEWS_EXEC", tier="pro")
        self.assertEqual(ctx.exception.reason, "tier_not_allowed")

    def test_scheduler_send_path_skips_before_send_when_matrix_blocks(self):
        with test_db() as db:
            user, sub, route = _seed_user(db, plan="elite")

            with patch("app.services.oracle_scheduler.send_thread_update") as send_thread_update_mock, patch(
                "app.services.oracle_scheduler.consume_usage"
            ) as consume_usage_mock:
                ok, status = _send_thread_message_with_quota(
                    db,
                    user=user,
                    route=route,
                    sub=sub,
                    plan="elite",
                    run=None,
                    run_id=uuid.uuid4(),
                    source="admin_broadcast",
                    symbol="GBPUSD",
                    title="Test",
                    body="Test message",
                    date_uk=date.today(),
                    strategy_name="VOL_MANIP",
                    dedupe_on_run=False,
                )

            self.assertFalse(ok)
            self.assertEqual(status, "strategy_blocked")
            send_thread_update_mock.assert_not_called()
            consume_usage_mock.assert_not_called()

            row = db.query(DeliveryLog).filter(DeliveryLog.user_id == user.id).first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.send_status, "SKIPPED")
            self.assertTrue((row.detail or "").startswith("strategy_matrix_blocked:"))


if __name__ == "__main__":
    unittest.main()

