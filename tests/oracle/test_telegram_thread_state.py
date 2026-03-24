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
from app.db.models import TelegramThreadState
from app.services.telegram_service import ensure_pinned_bias, send_thread_update


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


class TelegramThreadStateTests(unittest.TestCase):
    def test_ensure_pinned_bias_creates_one_row_per_chat_symbol_day(self):
        with test_db() as db:
            with patch("app.services.telegram_service.send_message", return_value=345) as send_message_mock, patch(
                "app.services.telegram_service.pin_message"
            ) as pin_mock:
                first = ensure_pinned_bias(
                    db,
                    chat_id="123456",
                    symbol="XAUUSD",
                    date_uk=date(2026, 2, 16),
                    anchor_text="DAILY BIAS",
                    pin_bool=True,
                )
                second = ensure_pinned_bias(
                    db,
                    chat_id="123456",
                    symbol="XAUUSD",
                    date_uk=date(2026, 2, 16),
                    anchor_text="DAILY BIAS",
                    pin_bool=True,
                )

            self.assertEqual(first, 345)
            self.assertEqual(second, 345)
            send_message_mock.assert_called_once()
            pin_mock.assert_called_once()
            rows = db.query(TelegramThreadState).all()
            self.assertEqual(len(rows), 1)

    def test_send_thread_update_replies_under_pinned_anchor(self):
        with test_db() as db:
            user_id = uuid.uuid4()
            with patch("app.services.telegram_service.send_message", side_effect=[777, 888]) as send_message_mock, patch(
                "app.services.telegram_service.pin_message"
            ):
                result = send_thread_update(
                    db,
                    user_id=user_id,
                    chat_id="654321",
                    symbol="GBPUSD",
                    date_uk=date(2026, 2, 16),
                    title="Trade Update",
                    body="Outcome: TP1\nTimestamp: 2026-02-16 09:30 UK\nReason: Follow-through.",
                    time_london="2026-02-16 09:30 UK",
                    pin_bool=True,
                )

            self.assertEqual(result["anchor_message_id"], 777)
            self.assertEqual(result["message_id"], 888)
            self.assertEqual(send_message_mock.call_count, 2)
            rows = db.query(TelegramThreadState).all()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].pinned_message_id, 777)


if __name__ == "__main__":
    unittest.main()

