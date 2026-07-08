from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.runner import mt5_runner
from app.runner.mt5_runner import _resolve_symbol_config


class MT5RunnerSymbolConfigTests(unittest.TestCase):
    def test_runner_symbols_take_precedence(self):
        with patch.dict(
            os.environ,
            {
                "RUNNER_SYMBOLS": "EURUSD, GBPJPY",
                "ORACLE_ENABLED_SYMBOLS": "XAUUSD, BTCUSD",
                "ORACLE_SYMBOL": "XAUUSD",
            },
            clear=True,
        ):
            config = _resolve_symbol_config()

        self.assertEqual(config["symbols"], ["EURUSD", "GBPJPY"])
        self.assertEqual(config["raw_env_value"], "EURUSD, GBPJPY")
        self.assertEqual(config["resolved_path"], "RUNNER_SYMBOLS")
        self.assertFalse(config["used_fallback"])

    def test_oracle_enabled_symbols_is_used_before_oracle_symbol(self):
        with patch.dict(
            os.environ,
            {
                "ORACLE_ENABLED_SYMBOLS": "XAUUSD, GBPJPY",
                "ORACLE_SYMBOL": "GBPJPY",
            },
            clear=True,
        ):
            config = _resolve_symbol_config()

        self.assertEqual(config["symbols"], ["XAUUSD", "GBPJPY"])
        self.assertEqual(config["resolved_path"], "ORACLE_ENABLED_SYMBOLS")
        self.assertFalse(config["used_fallback"])

    def test_oracle_symbol_supports_single_and_csv_values(self):
        with patch.dict(os.environ, {"ORACLE_SYMBOL": "XAUUSD, GBPJPY, xauusd"}, clear=True):
            config = _resolve_symbol_config()

        self.assertEqual(config["symbols"], ["XAUUSD", "GBPJPY"])
        self.assertEqual(config["raw_env_value"], "XAUUSD, GBPJPY, xauusd")
        self.assertEqual(config["resolved_path"], "ORACLE_SYMBOL")
        self.assertFalse(config["used_fallback"])

    def test_symbols_fall_back_to_xauusd_when_unset_or_blank(self):
        with patch.dict(
            os.environ,
            {
                "RUNNER_SYMBOLS": " ",
                "ORACLE_ENABLED_SYMBOLS": " , ",
                "ORACLE_SYMBOL": "",
            },
            clear=True,
        ):
            config = _resolve_symbol_config()

        self.assertEqual(config["symbols"], ["XAUUSD"])
        self.assertIsNone(config["raw_env_value"])
        self.assertEqual(config["resolved_path"], "default:XAUUSD")
        self.assertTrue(config["used_fallback"])

    def test_runner_startup_emits_multi_symbol_config(self):
        events: list[tuple[str, dict | None]] = []

        class StopLoop(Exception):
            pass

        class DummyMT5Manager:
            def __init__(self, **kwargs):
                self.connected = False

            def connect(self, *, force: bool = False) -> bool:
                return False

            def disconnect(self) -> None:
                return None

        cfg = {
            "api_base": "http://localhost:8000",
            "runner_key": "runner-key",
            "mt5_path": "terminal.exe",
            "mt5_login": "12345",
            "mt5_password": "secret",
            "mt5_server": "Broker-Demo",
            "runner_id": "mt5-runner-test",
            "runner_version": "1.0.0",
            "request_timeout_seconds": 30,
            "loop_seconds": 60,
            "heartbeat_seconds": 30,
            "conn_check_seconds": 20,
            "control_enabled": False,
            "control_host": "127.0.0.1",
            "control_port": 8787,
            "control_require_key": True,
            "broker_utc_offset_minutes": 0,
            "london_tz": None,
            "london_tz_name": "Europe/London",
        }

        with patch.dict(os.environ, {"RUNNER_SYMBOLS": "XAUUSD, GBPJPY"}, clear=True), patch.object(
            mt5_runner, "_validate_env", return_value=cfg
        ), patch.object(
            mt5_runner, "MT5Manager", DummyMT5Manager
        ), patch.object(
            mt5_runner.requests, "Session", return_value=object()
        ), patch.object(
            mt5_runner, "_send_heartbeat", return_value=None
        ), patch.object(
            mt5_runner, "_emit", side_effect=lambda event, payload=None: events.append((event, payload))
        ), patch.object(
            mt5_runner.time, "sleep", side_effect=StopLoop
        ):
            with self.assertRaises(StopLoop):
                mt5_runner.run_forever()

        emitted = {event: payload for event, payload in events if payload is not None}
        self.assertEqual(emitted["runner.symbols.config"]["raw_env_value"], "XAUUSD, GBPJPY")
        self.assertEqual(emitted["runner.symbols.config"]["symbols"], ["XAUUSD", "GBPJPY"])
        self.assertEqual(emitted["runner.started"]["symbols"], ["XAUUSD", "GBPJPY"])
        self.assertEqual(emitted["runner.started"]["symbols_resolved_path"], "RUNNER_SYMBOLS")


if __name__ == "__main__":
    unittest.main()
