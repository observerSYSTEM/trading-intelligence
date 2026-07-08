from __future__ import annotations

import sys
import types
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

jwt_stub = types.ModuleType("jwt")
jwt_stub.encode = lambda *args, **kwargs: "token"
jwt_stub.decode = lambda *args, **kwargs: {"sub": "stub@example.com"}
sys.modules.setdefault("jwt", jwt_stub)

from app.api.me import me
from app.db.base import Base
from app.db.models import Subscription, User

FULL_SYMBOLS_ENV = {"ORACLE_ENABLED_SYMBOLS": "XAUUSD,GBPUSD,EURUSD,GBPJPY,BTCUSD"}


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


def _mk_user(db: Session, *, email: str, role: str, plan: str, status: str) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        password_hash="hash",
        role=role,
        is_active=True,
    )
    db.add(user)
    db.flush()
    db.add(Subscription(user_id=user.id, plan=plan, status=status))
    db.commit()
    db.refresh(user)
    return user


@patch.dict("os.environ", FULL_SYMBOLS_ENV, clear=False)
class MeRouteTests(unittest.TestCase):
    def test_admin_me_returns_elite_active_entitlements_for_dev_testing(self):
        with test_db() as db:
            user = _mk_user(
                db,
                email="admin@test.com",
                role="admin",
                plan="basic",
                status="inactive",
            )

            payload = me(user=user, db=db, _limit=None)

            self.assertEqual(payload["tier"], "elite")
            self.assertEqual(payload["status"], "active")
            self.assertEqual(payload["symbols_available"], ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"])
            self.assertEqual(payload["symbols_enabled"], ["XAUUSD", "GBPJPY"])

    def test_non_admin_me_remains_plan_bound(self):
        with test_db() as db:
            user = _mk_user(
                db,
                email="user@test.com",
                role="user",
                plan="basic",
                status="inactive",
            )

            payload = me(user=user, db=db, _limit=None)

            self.assertEqual(payload["symbols_available"], ["XAUUSD"])
            self.assertEqual(payload["symbols_enabled"], ["XAUUSD"])

    def test_me_respects_configured_symbol_order_for_dashboard_selector(self):
        with test_db() as db:
            user = _mk_user(
                db,
                email="elite@test.com",
                role="user",
                plan="elite",
                status="active",
            )

            with patch.dict(
                "os.environ",
                {"ORACLE_ENABLED_SYMBOLS": "XAUUSD, GBPJPY, BTCUSD, EURUSD"},
                clear=True,
            ):
                payload = me(user=user, db=db, _limit=None)

            self.assertEqual(payload["symbols_available"], ["XAUUSD", "GBPJPY", "BTCUSD", "EURUSD"])
            self.assertEqual(payload["symbols_enabled"], ["XAUUSD", "GBPJPY"])


if __name__ == "__main__":
    unittest.main()

