from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.oracle_scheduler import _preferred_magnet_for_bias
from app.services.telegram_alerts import (
    maybe_send_daily_alignment_alert,
    maybe_send_liquidity_target_alert,
    record_signal_alert_sent,
    should_send_signal_alert,
)


class TelegramAlertTests(unittest.TestCase):
    def test_alert_dedupe_blocks_duplicates_and_allows_refresh_after_30m(self):
        state_path = Path.cwd() / "telegram_signal_alert_state.test.json"
        try:
            if state_path.exists():
                state_path.unlink()
            sent_at = datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc)

            with patch("app.services.telegram_alerts._state_path", return_value=state_path):
                first = should_send_signal_alert(
                    symbol="XAUUSD",
                    timeframe="M15",
                    bias="SELL_ONLY",
                    magnet=3334.25,
                    zone_target=3321.50,
                    signal_type="opportunity_m15_confirmed",
                    material_refresh=True,
                    now_utc=sent_at,
                )
                self.assertTrue(first["send"])
                self.assertEqual(first["reason"], "first_alert")

                record_signal_alert_sent(
                    symbol="XAUUSD",
                    timeframe="M15",
                    bias="SELL_ONLY",
                    magnet=3334.25,
                    zone_target=3321.50,
                    signal_type="opportunity_m15_confirmed",
                    sent_at=sent_at,
                )

                duplicate = should_send_signal_alert(
                    symbol="XAUUSD",
                    timeframe="M15",
                    bias="SELL_ONLY",
                    magnet=3334.25,
                    zone_target=3321.50,
                    signal_type="opportunity_m15_confirmed",
                    material_refresh=True,
                    now_utc=sent_at + timedelta(minutes=5),
                )
                self.assertFalse(duplicate["send"])
                self.assertEqual(duplicate["reason"], "alert_skipped_duplicate")

                late_refresh = should_send_signal_alert(
                    symbol="XAUUSD",
                    timeframe="M15",
                    bias="SELL_ONLY",
                    magnet=3334.25,
                    zone_target=3321.50,
                    signal_type="opportunity_m15_confirmed",
                    material_refresh=True,
                    now_utc=sent_at + timedelta(minutes=31),
                )
                self.assertTrue(late_refresh["send"])
                self.assertEqual(late_refresh["reason"], "refresh_after_30m")

                changed_key = should_send_signal_alert(
                    symbol="XAUUSD",
                    timeframe="M15",
                    bias="SELL_ONLY",
                    magnet=3334.25,
                    zone_target=3318.20,
                    signal_type="opportunity_m15_confirmed",
                    material_refresh=True,
                    now_utc=sent_at + timedelta(minutes=6),
                )
                self.assertTrue(changed_key["send"])
                self.assertEqual(changed_key["reason"], "dedupe_key_changed")
        finally:
            if state_path.exists():
                state_path.unlink()

    def test_directional_magnet_preference_matches_bias(self):
        liquidity = {
            "magnet_level": 3340.0,
            "sellside_liquidity": 3332.5,
            "buyside_liquidity": 3358.4,
        }

        self.assertEqual(_preferred_magnet_for_bias(liquidity, bias="SELL_ONLY"), 3332.5)
        self.assertEqual(_preferred_magnet_for_bias(liquidity, bias="BUY_ONLY"), 3358.4)
        self.assertEqual(_preferred_magnet_for_bias(liquidity, bias="NO_TRADE"), 3340.0)

    def test_daily_alignment_alert_sends_once_and_dedupes_on_official_alignment_only(self):
        state_path = Path.cwd() / "telegram_signal_alert_state.daily_alignment.test.json"
        detected_at = datetime(2026, 3, 23, 8, 5, tzinfo=timezone.utc)
        try:
            if state_path.exists():
                state_path.unlink()

            with (
                patch("app.services.telegram_alerts._state_path", return_value=state_path),
                patch("app.services.telegram_alerts.send_telegram_signal", return_value=True) as send_mock,
            ):
                first = maybe_send_daily_alignment_alert(
                    symbol="XAUUSD",
                    detected_at=detected_at,
                    permission_source="LONDON_0801",
                    permission_stage="OFFICIAL",
                    daily_permission="BUY_ONLY",
                    final_allowed="BUY_ONLY",
                    h1_confirmation=None,
                    m15_opportunity=None,
                    confidence=None,
                    reason="08:01 daily alignment confirmed",
                    magnet=None,
                    zone_target=None,
                    sellside=None,
                    buyside=None,
                    material_refresh=True,
                )
                self.assertEqual(first["status"], "alert_sent")
                self.assertEqual(send_mock.call_count, 1)
                sent_message = send_mock.call_args.args[0]
                self.assertIn("DAILY ALIGNMENT CONFIRMED - XAUUSD", sent_message)
                self.assertIn("Permission Source: LONDON_0801 (OFFICIAL)", sent_message)
                self.assertIn("Final Allowed: BUY_ONLY", sent_message)
                self.assertIn("H1 Confirmation: -", sent_message)
                self.assertIn("M15 Opportunity: -", sent_message)

                duplicate = maybe_send_daily_alignment_alert(
                    symbol="XAUUSD",
                    detected_at=detected_at + timedelta(minutes=5),
                    permission_source="LONDON_0801",
                    permission_stage="OFFICIAL",
                    daily_permission="BUY_ONLY",
                    final_allowed="BUY_ONLY",
                    h1_confirmation="CONFIRMED",
                    m15_opportunity="BUY_ONLY",
                    confidence=0.82,
                    reason="08:01 daily alignment confirmed",
                    magnet=3031.4,
                    zone_target=3042.0,
                    sellside=3018.2,
                    buyside=3050.8,
                    material_refresh=True,
                )
                self.assertEqual(duplicate["status"], "alert_skipped_duplicate")
                self.assertEqual(send_mock.call_count, 1)
        finally:
            if state_path.exists():
                state_path.unlink()

    def test_daily_alignment_alert_requires_london_0801_official_context(self):
        state_path = Path.cwd() / "telegram_signal_alert_state.daily_alignment.invalid.test.json"
        try:
            if state_path.exists():
                state_path.unlink()

            with (
                patch("app.services.telegram_alerts._state_path", return_value=state_path),
                patch("app.services.telegram_alerts.send_telegram_signal", return_value=True) as send_mock,
            ):
                result = maybe_send_daily_alignment_alert(
                    symbol="XAUUSD",
                    detected_at=datetime(2026, 3, 23, 7, 0, tzinfo=timezone.utc),
                    permission_source="ASIA",
                    permission_stage="PRELIM",
                    daily_permission="BUY_ONLY",
                    final_allowed="BUY_ONLY",
                    h1_confirmation="CONFIRMED",
                    m15_opportunity="BUY_ONLY",
                    confidence=0.82,
                    reason="prelim only",
                    magnet=3031.4,
                    zone_target=3042.0,
                    sellside=3018.2,
                    buyside=3050.8,
                    material_refresh=True,
                )
                self.assertEqual(result["status"], "not_applicable")
                self.assertEqual(result["reason"], "alignment_incomplete")
                send_mock.assert_not_called()
        finally:
            if state_path.exists():
                state_path.unlink()

    def test_liquidity_target_alert_sends_on_magnet_or_zone_change_only(self):
        state_path = Path.cwd() / "telegram_signal_alert_state.liquidity_target.test.json"
        as_of_utc = datetime(2026, 3, 23, 9, 15, tzinfo=timezone.utc)
        try:
            if state_path.exists():
                state_path.unlink()

            with (
                patch("app.services.telegram_alerts._state_path", return_value=state_path),
                patch("app.services.telegram_alerts.send_telegram_signal", return_value=True) as send_mock,
            ):
                first = maybe_send_liquidity_target_alert(
                    symbol="XAUUSD",
                    as_of_utc=as_of_utc,
                    reason="magnet recalculated",
                    magnet=3032.0,
                    zone_target=3044.5,
                    sellside=3017.0,
                    buyside=3053.0,
                    permission_source="LONDON_0801",
                    permission_stage="OFFICIAL",
                    final_allowed="BUY_ONLY",
                    h1_confirmation="CONFIRMED",
                    m15_opportunity="BUY_ONLY",
                    confidence=0.76,
                )
                self.assertEqual(first["status"], "alert_sent")
                self.assertEqual(send_mock.call_count, 1)
                sent_message = send_mock.call_args.args[0]
                self.assertIn("LIQUIDITY TARGET UPDATE - XAUUSD", sent_message)
                self.assertIn("Magnet: 3032.00", sent_message)
                self.assertIn("Zone Target: 3044.50", sent_message)
                self.assertIn("Sellside: 3017.00", sent_message)
                self.assertIn("Buyside: 3053.00", sent_message)

                duplicate = maybe_send_liquidity_target_alert(
                    symbol="XAUUSD",
                    as_of_utc=as_of_utc + timedelta(minutes=1),
                    reason="sellside moved only",
                    magnet=3032.0,
                    zone_target=3044.5,
                    sellside=3016.5,
                    buyside=3054.0,
                    permission_source="LONDON_0801",
                    permission_stage="OFFICIAL",
                    final_allowed="BUY_ONLY",
                    h1_confirmation="CONFIRMED",
                    m15_opportunity="BUY_ONLY",
                    confidence=0.76,
                )
                self.assertEqual(duplicate["status"], "alert_skipped_duplicate")
                self.assertEqual(send_mock.call_count, 1)

                changed = maybe_send_liquidity_target_alert(
                    symbol="XAUUSD",
                    as_of_utc=as_of_utc + timedelta(minutes=2),
                    reason="zone target changed",
                    magnet=3032.0,
                    zone_target=3046.0,
                    sellside=3016.5,
                    buyside=3054.0,
                    permission_source="LONDON_0801",
                    permission_stage="OFFICIAL",
                    final_allowed="BUY_ONLY",
                    h1_confirmation="CONFIRMED",
                    m15_opportunity="BUY_ONLY",
                    confidence=0.76,
                )
                self.assertEqual(changed["status"], "alert_sent")
                self.assertEqual(send_mock.call_count, 2)
        finally:
            if state_path.exists():
                state_path.unlink()


if __name__ == "__main__":
    unittest.main()
