from __future__ import annotations

import os
import sys
import types
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

apscheduler_module = types.ModuleType("apscheduler")
apscheduler_schedulers = types.ModuleType("apscheduler.schedulers")
apscheduler_background = types.ModuleType("apscheduler.schedulers.background")
apscheduler_background.BackgroundScheduler = object
apscheduler_triggers = types.ModuleType("apscheduler.triggers")
apscheduler_cron = types.ModuleType("apscheduler.triggers.cron")
apscheduler_cron.CronTrigger = object
apscheduler_date = types.ModuleType("apscheduler.triggers.date")
apscheduler_date.DateTrigger = object
sys.modules.setdefault("apscheduler", apscheduler_module)
sys.modules.setdefault("apscheduler.schedulers", apscheduler_schedulers)
sys.modules.setdefault("apscheduler.schedulers.background", apscheduler_background)
sys.modules.setdefault("apscheduler.triggers", apscheduler_triggers)
sys.modules.setdefault("apscheduler.triggers.cron", apscheduler_cron)
sys.modules.setdefault("apscheduler.triggers.date", apscheduler_date)

from app.services.oracle_scheduler import run_oracle_all_symbols_job
from app.services.oracle_scheduler import run_oracle_hourly_job


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def commit(self):
        return None


class OracleSymbolIterationTests(unittest.TestCase):
    def test_oracle_all_symbols_job_iterates_configured_symbols_in_order(self):
        visited: list[str] = []

        def _fake_run(symbol: str | None = None, *, dispatch_signals: bool = False) -> dict:
            visited.append(str(symbol))
            return {"ok": True, "symbol": symbol, "dispatch_signals": dispatch_signals}

        with patch.dict(
            os.environ,
            {"ORACLE_ENABLED_SYMBOLS": "XAUUSD, GBPJPY, BTCUSD, EURUSD"},
            clear=True,
        ), patch("app.services.oracle_scheduler.run_oracle_hourly_job", side_effect=_fake_run):
            result = run_oracle_all_symbols_job()

        self.assertTrue(result["ok"])
        self.assertEqual(visited, ["XAUUSD", "GBPJPY", "BTCUSD", "EURUSD"])
        self.assertEqual(
            [row["symbol"] for row in result["runs"]],
            ["XAUUSD", "GBPJPY", "BTCUSD", "EURUSD"],
        )

    def test_manual_run_passes_market_feed_timestamp_into_targets_refresh(self):
        feed_time = datetime(2026, 5, 1, 12, 4, tzinfo=timezone.utc)
        opp_time = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
        permission_row = SimpleNamespace(
            daily_permission="BUY_ONLY",
            as_of_utc=opp_time,
            daily_permission_stage="OFFICIAL",
            permission_source="LONDON_0801",
            for_date=date(2026, 5, 1),
            date_uk=date(2026, 5, 1),
        )
        opp = SimpleNamespace(
            public_json={},
            as_of_utc=opp_time,
            opportunity_direction="BUY_ONLY",
            final_allowed="BUY_ONLY",
            h1_confirm_ok=True,
            confidence=0.82,
        )
        weekly_snapshot = SimpleNamespace(
            symbol="XAUUSD",
            week_key="2026-W18",
            week_start_uk=date(2026, 4, 27),
            high=2350.0,
            low=2280.0,
            mid=2315.0,
            range_ready=True,
            as_of_utc=opp_time,
            meta_json={},
        )
        run = SimpleNamespace(id="run-1", symbol="XAUUSD", status="computed")
        refresh_calls: list[dict] = []

        def _fake_refresh(db, *, symbols, reason, tiers, as_of_utc=None):
            refresh_calls.append(
                {
                    "symbols": list(symbols),
                    "reason": reason,
                    "tiers": list(tiers),
                    "as_of_utc": as_of_utc,
                }
            )
            return [{"ok": True, "symbol": "XAUUSD", "tier": "pro", "as_of_utc": as_of_utc.isoformat()}]

        with patch("app.services.oracle_scheduler.SessionLocal", return_value=_FakeSession()), patch(
            "app.services.oracle_scheduler._ingest_oracle_source_candles",
            return_value=[{"ok": True, "symbol": "XAUUSD", "timeframe": "M15"}],
        ), patch(
            "app.services.oracle_scheduler._ensure_daily_permission_snapshot",
            return_value=(permission_row, {"ok": True}),
        ), patch(
            "app.services.oracle_scheduler.compute_opportunity_with_h1_confirmation",
            return_value=opp,
        ), patch(
            "app.services.oracle_scheduler._latest_daily_permission_snapshot",
            return_value=None,
        ), patch(
            "app.services.oracle_scheduler.compute_weekly_range_snapshot",
            return_value=weekly_snapshot,
        ), patch(
            "app.services.oracle_scheduler._upsert_weekly_range_snapshot",
            return_value=None,
        ), patch(
            "app.services.oracle_scheduler.latest_market_feed_freshness",
            return_value={
                "latest_market_feed_at": feed_time,
                "latest_market_feed_source": "ingest_status",
                "last_ingest_at": feed_time,
                "latest_candle_time": opp_time,
                "latest_candle_timeframe": "M15",
                "market_feed_age_seconds": 0,
                "market_feed_delayed": False,
                "market_feed_delay_reason": None,
                "market_feed_delay_threshold_seconds": 600,
            },
        ), patch(
            "app.services.oracle_scheduler._opportunity_to_oracle_run",
            return_value=run,
        ), patch(
            "app.services.oracle_scheduler.refresh_targets_for_all_symbols",
            side_effect=_fake_refresh,
        ):
            result = run_oracle_hourly_job(symbol="XAUUSD", dispatch_signals=False)

        self.assertEqual(len(refresh_calls), 1)
        self.assertEqual(refresh_calls[0]["as_of_utc"], feed_time)
        self.assertEqual(result["targets_refresh"][0]["as_of_utc"], feed_time.isoformat())
        self.assertEqual(result["targets_market_feed"]["latest_market_feed_at_utc"], feed_time.isoformat())


if __name__ == "__main__":
    unittest.main()
