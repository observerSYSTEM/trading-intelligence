import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.oracle import _daily_permission_health, _probe_api_anchor_candle
from app.db.base import Base
from app.services.data_provider import Candle


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


def _candle(symbol: str, timeframe: str, at: datetime) -> Candle:
    return Candle(
        symbol=symbol,
        timeframe=timeframe,
        time_utc=at,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        broker_symbol=symbol,
        volume=10,
        source="oanda",
        complete=True,
    )


class _FakeOandaProvider:
    name = "oanda"

    def __init__(self, responses: dict[str, list[Candle]]) -> None:
        self.responses = responses

    def get_candles_range(self, symbol: str, timeframe: str, start_utc: datetime, end_utc: datetime) -> list[Candle]:
        return list(self.responses.get(timeframe, []))


class _FakeApiProvider:
    name = "api"

    def __init__(self, primary: _FakeOandaProvider) -> None:
        self.primary = primary
        self.fallback = None


class ApiAnchorFallbackTests(unittest.TestCase):
    def test_m5_fallback_supplies_anchor_when_m1_missing(self):
        target = datetime(2026, 6, 11, 7, 1, tzinfo=timezone.utc)
        provider = _FakeApiProvider(
            _FakeOandaProvider(
                {
                    "M1": [],
                    "M5": [_candle("XAUUSD", "M5", target - timedelta(minutes=1))],
                }
            )
        )

        with patch("app.api.oracle.get_data_provider", return_value=provider):
            result = _probe_api_anchor_candle("XAUUSD", target_utc=target)

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "m5_fallback_ok")
        self.assertEqual(result["timeframe"], "M5")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["source"], "oanda")

    def test_api_feed_fresh_prevents_degraded_missing_anchor(self):
        now_utc = datetime(2026, 6, 11, 8, 30, tzinfo=timezone.utc)

        with test_db() as db:
            with patch("app.api.oracle.api_candle_mode", return_value=True), patch(
                "app.api.oracle._probe_api_anchor_candle",
                return_value={
                    "ok": False,
                    "status": "fetch_failed",
                    "source": "oanda",
                    "time_utc": None,
                    "time_utc_iso": None,
                    "error": "M1 unavailable",
                },
            ), patch(
                "app.api.oracle._probe_latest_api_candle",
                return_value={
                    "ok": True,
                    "source": "oanda",
                    "time_utc": now_utc - timedelta(minutes=5),
                    "time_utc_iso": (now_utc - timedelta(minutes=5)).isoformat(),
                    "error": None,
                },
            ):
                result = _daily_permission_health(db, "XAUUSD", now_utc=now_utc)

        self.assertTrue(result["missing"])
        self.assertFalse(result["degraded"])
        self.assertIsNone(result["reason"])


if __name__ == "__main__":
    unittest.main()
