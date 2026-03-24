from __future__ import annotations

import asyncio
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.requests import Request

from app.api.billing import stripe_webhook
from app.api.deps import require_admin
from app.api.notifications import get_notifications
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit, reset_rate_limit_state
from app.db.base import Base
from app.db.models import NotificationRoute, User


@contextmanager
def test_db():
    engine = create_engine("sqlite:///:memory:")
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db: Session = TestingSessionLocal()
    try:
        yield db
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


class SecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_rate_limit_state()

    def test_webhook_missing_signature_returns_400(self):
        with test_db() as db:
            settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            req = _request("/billing/webhook", headers=[])
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(stripe_webhook(req, db))
            self.assertEqual(ctx.exception.status_code, 400)

    def test_webhook_duplicate_event_id_returns_200_noop(self):
        with test_db() as db:
            settings.STRIPE_WEBHOOK_SECRET = "whsec_test"
            fake_event = {
                "id": "evt_dup_1",
                "type": "checkout.session.completed",
                "data": {"object": {"metadata": {"user_id": str(uuid.uuid4()), "plan": "basic"}, "customer": "cus_1", "subscription": "sub_1"}},
            }
            headers = [(b"stripe-signature", b"sig")]
            req1 = _request("/billing/webhook", headers=headers)
            req2 = _request("/billing/webhook", headers=headers)
            with patch("app.api.billing.stripe.Webhook.construct_event", return_value=fake_event):
                first = asyncio.run(stripe_webhook(req1, db))
                second = asyncio.run(stripe_webhook(req2, db))
            self.assertTrue(first.get("ok"))
            self.assertTrue(second.get("ok"))
            self.assertTrue(second.get("duplicate"))

    def test_require_admin_blocks_non_admin(self):
        user = User(
            id=uuid.uuid4(),
            email="user@test.com",
            password_hash="hash",
            role="user",
            is_active=True,
        )
        with self.assertRaises(HTTPException) as ctx:
            require_admin(user=user)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_rate_limit_throttles_after_limit(self):
        limiter_dep = rate_limit("auth_token_test", (RateLimitRule(limit=5, window_seconds=60),))
        dep_fn = limiter_dep.dependency
        assert dep_fn is not None
        req = _request("/auth/token")
        for _ in range(5):
            dep_fn(req)
        with self.assertRaises(HTTPException) as ctx:
            dep_fn(req)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_bola_notifications_uses_current_user_only(self):
        with test_db() as db:
            user_a = User(
                id=uuid.uuid4(),
                email="a@test.com",
                password_hash="hash",
                role="user",
                is_active=True,
            )
            user_b = User(
                id=uuid.uuid4(),
                email="b@test.com",
                password_hash="hash",
                role="user",
                is_active=True,
            )
            db.add(user_a)
            db.add(user_b)
            db.add(NotificationRoute(user_id=user_a.id, telegram_enabled=True, telegram_chat_id="123456789"))
            db.add(NotificationRoute(user_id=user_b.id, telegram_enabled=True, telegram_chat_id="999999999"))
            db.commit()

            payload = get_notifications(user=user_a, db=db)
            self.assertNotEqual(payload.get("telegram_chat_id"), "999999999")
            self.assertTrue(payload.get("telegram_chat_id", "").startswith("12"))


if __name__ == "__main__":
    unittest.main()
