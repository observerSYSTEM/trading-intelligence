from __future__ import annotations

import asyncio
import unittest
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.api.billing import stripe_webhook
from app.core.config import settings
from app.db.base import Base
from app.db.models import DeliveryLog, NotificationRoute, Subscription, User
from app.services.oracle_scheduler import _run_billing_renewal_reminder_job


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
        yield db, testing_session_local
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)


def _request(path: str, headers: list[tuple[bytes, bytes]] | None = None, body: bytes = b"{}") -> Request:
    hdrs = headers or []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": hdrs,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    return Request(scope, receive)


def _to_unix(dt: datetime | None) -> int | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class BillingWebhookAndReminderTests(unittest.TestCase):
    def test_invoice_payment_failed_sets_past_due_and_sends_telegram(self):
        with test_db() as (db, _):
            settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            user_id = uuid.uuid4()
            user = User(id=user_id, email="failed@test.com", password_hash="hash", role="user", is_active=True)
            sub = Subscription(
                user_id=user_id,
                plan="pro",
                status="active",
                stripe_customer_id="cus_failed",
                stripe_subscription_id="sub_failed",
            )
            route = NotificationRoute(user_id=user_id, telegram_enabled=True, telegram_chat_id="123456789")
            db.add_all([user, sub, route])
            db.commit()

            period_end = int((datetime.now(timezone.utc) + timedelta(days=28)).timestamp())
            event = {
                "id": "evt_invoice_failed_1",
                "type": "invoice.payment_failed",
                "data": {
                    "object": {
                        "subscription": "sub_failed",
                        "customer": "cus_failed",
                        "period_end": period_end,
                    }
                },
            }
            req = _request("/billing/webhook", headers=[(b"stripe-signature", b"sig")])

            with patch("app.api.billing.stripe.Webhook.construct_event", return_value=event):
                with patch("app.api.billing.send_telegram_message", return_value=1) as send_mock:
                    payload = asyncio.run(stripe_webhook(req, db))

            db.refresh(sub)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(sub.status, "past_due")
            self.assertEqual(_to_unix(sub.current_period_end), period_end)
            self.assertEqual(send_mock.call_count, 1)
            sent_text = send_mock.call_args.args[1]
            self.assertIn("Open dashboard", sent_text)
            self.assertIn("Manage Billing", sent_text)

    def test_invoice_payment_succeeded_sets_active_and_updates_period_end(self):
        with test_db() as (db, _):
            settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            user_id = uuid.uuid4()
            user = User(id=user_id, email="success@test.com", password_hash="hash", role="user", is_active=True)
            sub = Subscription(
                user_id=user_id,
                plan="pro",
                status="past_due",
                stripe_customer_id="cus_success",
                stripe_subscription_id="sub_success",
            )
            route = NotificationRoute(user_id=user_id, telegram_enabled=True, telegram_chat_id="123456789")
            db.add_all([user, sub, route])
            db.commit()

            period_end = int((datetime.now(timezone.utc) + timedelta(days=31)).timestamp())
            event = {
                "id": "evt_invoice_success_1",
                "type": "invoice.payment_succeeded",
                "data": {
                    "object": {
                        "subscription": "sub_success",
                        "customer": "cus_success",
                        "period_end": period_end,
                    }
                },
            }
            req = _request("/billing/webhook", headers=[(b"stripe-signature", b"sig")])

            with patch("app.api.billing.stripe.Webhook.construct_event", return_value=event):
                with patch("app.api.billing.send_telegram_message", return_value=1) as send_mock:
                    payload = asyncio.run(stripe_webhook(req, db))

            db.refresh(sub)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(sub.status, "active")
            self.assertEqual(_to_unix(sub.current_period_end), period_end)
            self.assertEqual(send_mock.call_count, 1)

    def test_subscription_deleted_sets_canceled_and_downgrades_basic(self):
        with test_db() as (db, _):
            settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            user_id = uuid.uuid4()
            user = User(id=user_id, email="deleted@test.com", password_hash="hash", role="user", is_active=True)
            sub = Subscription(
                user_id=user_id,
                plan="elite",
                status="active",
                stripe_customer_id="cus_deleted",
                stripe_subscription_id="sub_deleted",
            )
            db.add_all([user, sub])
            db.commit()

            event = {
                "id": "evt_subscription_deleted_1",
                "type": "customer.subscription.deleted",
                "data": {"object": {"id": "sub_deleted", "customer": "cus_deleted"}},
            }
            req = _request("/billing/webhook", headers=[(b"stripe-signature", b"sig")])

            with patch("app.api.billing.stripe.Webhook.construct_event", return_value=event):
                payload = asyncio.run(stripe_webhook(req, db))

            db.refresh(sub)
            self.assertTrue(payload.get("ok"))
            self.assertEqual(sub.status, "canceled")
            self.assertEqual(sub.plan, "basic")
            self.assertIsNone(sub.stripe_subscription_id)

    def test_renewal_reminder_dedupes_within_three_day_window(self):
        with test_db() as (_, testing_session_local):
            now = datetime.now(timezone.utc)
            with testing_session_local() as db:
                user_id = uuid.uuid4()
                user = User(id=user_id, email="renew@test.com", password_hash="hash", role="user", is_active=True)
                sub = Subscription(
                    user_id=user_id,
                    plan="pro",
                    status="active",
                    current_period_end=now + timedelta(days=2),
                )
                route = NotificationRoute(user_id=user_id, telegram_enabled=True, telegram_chat_id="555000111")
                db.add_all([user, sub, route])
                db.commit()

            with patch("app.services.oracle_scheduler.SessionLocal", testing_session_local):
                with patch("app.services.oracle_scheduler.send_telegram_message", return_value=99) as send_mock:
                    _run_billing_renewal_reminder_job()
                    _run_billing_renewal_reminder_job()

            with testing_session_local() as db:
                sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
                sent_logs = (
                    db.query(DeliveryLog)
                    .filter(
                        DeliveryLog.user_id == user_id,
                        DeliveryLog.source == "billing_renewal_reminder",
                        DeliveryLog.send_status == "SENT",
                    )
                    .all()
                )
                self.assertIsNotNone(sub)
                assert sub is not None
                self.assertIsNotNone(sub.last_renewal_reminder_at)
                self.assertEqual(send_mock.call_count, 1)
                self.assertEqual(len(sent_logs), 1)


if __name__ == "__main__":
    unittest.main()
