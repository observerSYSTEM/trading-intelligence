from __future__ import annotations

import sys
import types
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

jwt_stub = types.ModuleType("jwt")
jwt_stub.encode = lambda *args, **kwargs: "token"
jwt_stub.decode = lambda *args, **kwargs: {"sub": "stub@example.com"}
sys.modules.setdefault("jwt", jwt_stub)

from app.api.symbols import SymbolPreferencesIn, put_symbols_preferences
from app.db.base import Base
from app.db.models import Subscription, User, UserSignalPref, UserSymbolPreference
from app.services.symbol_preferences import get_user_enabled_symbols

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


@patch.dict("os.environ", FULL_SYMBOLS_ENV, clear=False)
class SymbolPreferenceTests(unittest.TestCase):
    def test_elite_user_defaults_include_gbpjpy_without_saved_preferences(self):
        with test_db() as db:
            user = _mk_user(db, "elite-default@test.com", plan="elite")

            selected = get_user_enabled_symbols(db, user.id, "elite")

            self.assertEqual(selected, ["XAUUSD", "GBPJPY"])

    def test_saved_preferences_still_override_elite_default_symbols(self):
        with test_db() as db:
            user = _mk_user(db, "elite-saved@test.com", plan="elite")
            db.add(UserSignalPref(user_id=user.id, symbols_json=["XAUUSD"]))
            db.commit()

            selected = get_user_enabled_symbols(db, user.id, "elite")

            self.assertEqual(selected, ["XAUUSD"])

    def test_basic_user_cannot_enable_eurusd(self):
        with test_db() as db:
            user = _mk_user(db, "basic@test.com", plan="basic")
            payload = SymbolPreferencesIn(selected=["XAUUSD", "EURUSD"])

            with self.assertRaises(HTTPException) as ctx:
                put_symbols_preferences(payload=payload, user=user, db=db, _limit=None)

            self.assertEqual(ctx.exception.status_code, 403)
            detail = ctx.exception.detail
            self.assertEqual(detail.get("error"), "locked_symbols")
            self.assertIn("EURUSD", detail.get("locked", []))

    def test_pro_user_can_enable_eurusd_but_not_btcusd(self):
        with test_db() as db:
            user = _mk_user(db, "pro@test.com", plan="pro")
            payload = SymbolPreferencesIn(selected=["XAUUSD", "EURUSD", "BTCUSD"])

            with self.assertRaises(HTTPException) as ctx:
                put_symbols_preferences(payload=payload, user=user, db=db, _limit=None)

            self.assertEqual(ctx.exception.status_code, 403)
            detail = ctx.exception.detail
            self.assertIn("BTCUSD", detail.get("locked", []))

    def test_elite_user_can_enable_all(self):
        with test_db() as db:
            user = _mk_user(db, "elite@test.com", plan="elite")
            payload = SymbolPreferencesIn(selected=["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"])

            result = put_symbols_preferences(payload=payload, user=user, db=db, _limit=None)
            self.assertEqual(result["selected"], ["XAUUSD", "GBPUSD", "EURUSD", "GBPJPY", "BTCUSD"])

            rows = db.query(UserSymbolPreference).filter(UserSymbolPreference.user_id == user.id).all()
            enabled = sorted([row.symbol for row in rows if row.enabled])
            self.assertEqual(enabled, ["BTCUSD", "EURUSD", "GBPJPY", "GBPUSD", "XAUUSD"])

    def test_payload_rejects_user_id_field(self):
        with self.assertRaises(ValidationError):
            SymbolPreferencesIn.model_validate({"selected": ["XAUUSD"], "user_id": str(uuid.uuid4())})

    def test_unique_constraint_blocks_duplicate_rows(self):
        with test_db() as db:
            user = _mk_user(db, "dup@test.com", plan="elite")
            db.add(UserSymbolPreference(user_id=user.id, symbol="XAUUSD", enabled=True))
            db.commit()

            db.add(UserSymbolPreference(user_id=user.id, symbol="XAUUSD", enabled=False))
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

    def test_user_cannot_change_other_user_preferences(self):
        with test_db() as db:
            user_a = _mk_user(db, "a@test.com", plan="elite")
            user_b = _mk_user(db, "b@test.com", plan="elite")
            db.add(UserSymbolPreference(user_id=user_b.id, symbol="XAUUSD", enabled=False))
            db.commit()

            payload = SymbolPreferencesIn(selected=["XAUUSD", "EURUSD"])
            put_symbols_preferences(payload=payload, user=user_a, db=db, _limit=None)

            rows_b = db.query(UserSymbolPreference).filter(UserSymbolPreference.user_id == user_b.id).all()
            # unchanged row for user_b
            self.assertEqual(len(rows_b), 1)
            self.assertFalse(rows_b[0].enabled)


if __name__ == "__main__":
    unittest.main()
