from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.h4_session_modifier import apply_h4_session_flip_modifier


def _candle(
    *,
    time_open_utc: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> dict:
    return {
        "time_open_utc": time_open_utc,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class H4SessionModifierTests(unittest.TestCase):
    def test_non_key_window_no_change(self):
        last = _candle(
            time_open_utc=datetime(2026, 2, 16, 2, 0, tzinfo=timezone.utc),
            open_=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            volume=1000.0,
        )
        prev = _candle(
            time_open_utc=datetime(2026, 2, 15, 22, 0, tzinfo=timezone.utc),
            open_=98.0,
            high=101.0,
            low=97.0,
            close=100.0,
            volume=900.0,
        )
        result = apply_h4_session_flip_modifier(
            symbol="XAUUSD",
            allowed_direction="BUY_ONLY",
            confidence=0.55,
            last_h4_candle=last,
            prev_h4_candle=prev,
        )
        self.assertFalse(result.applied)
        self.assertAlmostEqual(result.modified_confidence, 0.55, places=6)
        self.assertEqual(result.reasons_public, [])

    def test_confirm_buy_with_volume_spike_and_supportive_sweep(self):
        # Summer date: 16:00 UTC == 17:00 UK (BST), key window.
        open_time = datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)
        last = _candle(
            time_open_utc=open_time,
            open_=100.0,
            high=110.0,
            low=99.0,
            close=108.0,  # bullish, upper-half close
            volume=130.0,
        )
        prev = _candle(
            time_open_utc=open_time - timedelta(hours=4),
            open_=95.0,
            high=101.0,
            low=94.0,
            close=100.0,
            volume=100.0,
        )
        result = apply_h4_session_flip_modifier(
            symbol="XAUUSD",
            allowed_direction="BUY_ONLY",
            confidence=0.50,
            last_h4_candle=last,
            prev_h4_candle=prev,
            liquidity_last_sweep={
                "side": "sellside",
                "time_utc": (open_time + timedelta(hours=1)).isoformat(),
            },
        )
        # +0.06 * 1.25 + 0.02 = +0.095
        self.assertTrue(result.applied)
        self.assertAlmostEqual(result.modified_confidence, 0.595, places=6)
        self.assertIn("Key H4 window (UK): 17:00 candle aligned with bias.", result.reasons_public)
        self.assertIn("Volume expanded during key H4 candle — higher conviction.", result.reasons_public)
        self.assertIn("Recent liquidity sweep supports the bias.", result.reasons_public)

    def test_reject_sell_with_vol_drop_and_conflicting_sweep(self):
        open_time = datetime(2026, 2, 16, 17, 0, tzinfo=timezone.utc)  # 17:00 UK in winter
        last = _candle(
            time_open_utc=open_time,
            open_=100.0,
            high=111.0,
            low=99.0,
            close=110.0,  # bullish, upper-half close => reject SELL
            volume=80.0,
        )
        prev = _candle(
            time_open_utc=open_time - timedelta(hours=4),
            open_=97.0,
            high=102.0,
            low=96.0,
            close=101.0,
            volume=100.0,
        )
        result = apply_h4_session_flip_modifier(
            symbol="GBPUSD",
            allowed_direction="SELL_ONLY",
            confidence=0.70,
            last_h4_candle=last,
            prev_h4_candle=prev,
            liquidity_last_sweep={
                "side": "sellside",  # conflicts with SELL support-side (buyside)
                "time_utc": (open_time + timedelta(hours=2)).isoformat(),
            },
        )
        # -0.08 * 0.85 - 0.02 = -0.088
        self.assertTrue(result.applied)
        self.assertAlmostEqual(result.modified_confidence, 0.612, places=6)
        self.assertIn("Key H4 window (UK): candle rejected the current bias — caution.", result.reasons_public)
        self.assertIn("Volume lighter during key H4 candle — lower conviction.", result.reasons_public)
        self.assertIn("Recent liquidity sweep conflicts with the bias.", result.reasons_public)

    def test_no_trade_only_informational_reason(self):
        open_time = datetime(2026, 2, 16, 1, 0, tzinfo=timezone.utc)
        last = _candle(
            time_open_utc=open_time,
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.2,
            volume=100.0,
        )
        prev = _candle(
            time_open_utc=open_time - timedelta(hours=4),
            open_=99.5,
            high=100.5,
            low=98.5,
            close=99.9,
            volume=100.0,
        )
        result = apply_h4_session_flip_modifier(
            symbol="EURUSD",
            allowed_direction="NO_TRADE",
            confidence=0.33,
            last_h4_candle=last,
            prev_h4_candle=prev,
        )
        self.assertFalse(result.applied)
        self.assertAlmostEqual(result.modified_confidence, 0.33, places=6)
        self.assertIn("Key H4 window (UK): 01:00 informational check only.", result.reasons_public)

    def test_confidence_clamps_at_099(self):
        open_time = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)  # 09:00 UK BST
        last = _candle(
            time_open_utc=open_time,
            open_=100.0,
            high=109.0,
            low=99.0,
            close=108.0,
            volume=150.0,
        )
        prev = _candle(
            time_open_utc=open_time - timedelta(hours=4),
            open_=99.0,
            high=101.0,
            low=98.0,
            close=100.0,
            volume=100.0,
        )
        result = apply_h4_session_flip_modifier(
            symbol="BTCUSD",
            allowed_direction="BUY_ONLY",
            confidence=0.98,
            last_h4_candle=last,
            prev_h4_candle=prev,
        )
        self.assertAlmostEqual(result.modified_confidence, 0.99, places=6)


if __name__ == "__main__":
    unittest.main()
