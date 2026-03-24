from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.settings import TelegramSettingsIn, set_telegram_settings
from app.db.base import Base
from app.db.models import NotificationRoute, Subscription, User, UserSignalPref


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


def _mk_user(db: Session, email: str, *, plan: str = "elite") -> User:
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


class TelegramSettingsUpsertTests(unittest.TestCase):
    def test_settings_save_is_idempotent_upsert(self):
        with test_db() as db:
            user = _mk_user(db, "telegram@test.com", plan="elite")

            first = TelegramSettingsIn(
                telegram_enabled=True,
                telegram_chat_id="123456789",
                symbols=["XAUUSD", "GBPUSD"],
            )
            first_res = set_telegram_settings(payload=first, user=user, db=db, _limit=None)
            self.assertTrue(first_res["ok"])
            self.assertEqual(first_res["telegram_chat_id"], "123456789")
            self.assertEqual(first_res["symbols"], ["XAUUSD", "GBPUSD"])

            second = TelegramSettingsIn(
                telegram_enabled=True,
                telegram_chat_id="987654321",
                symbols=["XAUUSD", "BTCUSD"],
            )
            second_res = set_telegram_settings(payload=second, user=user, db=db, _limit=None)
            self.assertTrue(second_res["ok"])
            self.assertEqual(second_res["telegram_chat_id"], "987654321")
            self.assertEqual(second_res["symbols"], ["XAUUSD", "BTCUSD"])

            pref_rows = db.query(UserSignalPref).filter(UserSignalPref.user_id == user.id).all()
            self.assertEqual(len(pref_rows), 1)
            self.assertEqual((pref_rows[0].telegram_chat_id or "").strip(), "987654321")
            self.assertEqual(pref_rows[0].symbols_json, ["XAUUSD", "BTCUSD"])

            route_rows = db.query(NotificationRoute).filter(NotificationRoute.user_id == user.id).all()
            self.assertEqual(len(route_rows), 1)
            self.assertTrue(bool(route_rows[0].telegram_enabled))

    def test_enabling_without_chat_id_is_rejected(self):
        with test_db() as db:
            user = _mk_user(db, "telegram2@test.com", plan="basic")
            with self.assertRaises(HTTPException) as ctx:
                set_telegram_settings(
                    payload=TelegramSettingsIn(telegram_enabled=True),
                    user=user,
                    db=db,
                    _limit=None,
                )
            self.assertEqual(ctx.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

