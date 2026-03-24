from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Subscription, User
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage


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


def _mk_user(db: Session, email: str, *, plan: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, plan=plan, status="active"))
    db.commit()
    db.refresh(user)
    return user


class UsageLimitTests(unittest.TestCase):
    def test_basic_over_limit_blocks(self):
        with test_db() as db:
            user = _mk_user(db, "basic-usage@test.com", plan="basic")
            for i in range(30):
                consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="test_basic",
                    symbol="XAUUSD",
                    signal_id=f"basic-{i}",
                )
            db.commit()

            usage = get_usage(db, user.id)
            self.assertEqual(usage["tier"], "basic")
            self.assertEqual(usage["limit"], 30)
            self.assertEqual(usage["used"], 30)
            self.assertEqual(usage["remaining"], 0)

            with self.assertRaises(UsageLimitExceeded):
                consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="test_basic_over",
                    symbol="XAUUSD",
                    signal_id="basic-over",
                )

    def test_pro_over_limit_blocks(self):
        with test_db() as db:
            user = _mk_user(db, "pro-usage@test.com", plan="pro")
            for i in range(120):
                consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="test_pro",
                    symbol="EURUSD",
                    signal_id=f"pro-{i}",
                )
            db.commit()

            usage = get_usage(db, user.id)
            self.assertEqual(usage["tier"], "pro")
            self.assertEqual(usage["limit"], 120)
            self.assertEqual(usage["used"], 120)
            self.assertEqual(usage["remaining"], 0)

            with self.assertRaises(UsageLimitExceeded):
                consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="test_pro_over",
                    symbol="EURUSD",
                    signal_id="pro-over",
                )

    def test_elite_unlimited_never_blocks(self):
        with test_db() as db:
            user = _mk_user(db, "elite-usage@test.com", plan="elite")
            for i in range(300):
                consume_usage(
                    db,
                    user.id,
                    n=1,
                    reason="test_elite",
                    symbol="BTCUSD",
                    signal_id=f"elite-{i}",
                )
            db.commit()

            usage = get_usage(db, user.id)
            self.assertEqual(usage["tier"], "elite")
            self.assertIsNone(usage["limit"])
            self.assertIsNone(usage["remaining"])
            self.assertEqual(usage["used"], 300)

            # Still does not block beyond former hard limits.
            consume_usage(
                db,
                user.id,
                n=1,
                reason="test_elite_more",
                symbol="BTCUSD",
                signal_id="elite-over-1000",
            )
            db.commit()
            usage_after = get_usage(db, user.id)
            self.assertEqual(usage_after["used"], 301)


if __name__ == "__main__":
    unittest.main()

