from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.core.symbols import (
    allowed_symbols_for_plan,
    configured_symbol_config,
    configured_symbol_map_from_env,
    resolve_mt5_broker_symbol,
)


class _FakeMatch:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeMT5:
    def __init__(self, *, successful_symbols: set[str], wildcard_matches: dict[str, list[str]] | None = None) -> None:
        self.successful_symbols = set(successful_symbols)
        self.wildcard_matches = dict(wildcard_matches or {})
        self.selected_attempts: list[str] = []

    def symbol_select(self, symbol: str, _selected: bool) -> bool:
        self.selected_attempts.append(symbol)
        return symbol in self.successful_symbols

    def symbols_get(self, pattern: str):
        return [_FakeMatch(name) for name in self.wildcard_matches.get(pattern, [])]

    @staticmethod
    def last_error():
        return "fake_last_error"


class SymbolConfigTests(unittest.TestCase):
    def test_configured_symbols_parse_csv_and_filter_by_plan(self):
        with patch.dict(
            os.environ,
            {"ORACLE_ENABLED_SYMBOLS": "XAUUSD, GBPJPY, BTCUSD, EURUSD"},
            clear=True,
        ):
            config = configured_symbol_config()
            elite_symbols = allowed_symbols_for_plan("elite")
            pro_symbols = allowed_symbols_for_plan("pro")

        self.assertEqual(config.symbols, ["XAUUSD", "GBPJPY", "BTCUSD", "EURUSD"])
        self.assertEqual(config.raw_env_value, "XAUUSD, GBPJPY, BTCUSD, EURUSD")
        self.assertEqual(config.resolved_path, "ORACLE_ENABLED_SYMBOLS")
        self.assertEqual(elite_symbols, ["XAUUSD", "GBPJPY", "BTCUSD", "EURUSD"])
        self.assertEqual(pro_symbols, ["XAUUSD", "EURUSD"])

    def test_configured_symbols_fall_back_to_oracle_symbol(self):
        with patch.dict(
            os.environ,
            {
                "ORACLE_ENABLED_SYMBOLS": " , ",
                "ORACLE_SYMBOL": "eurusd, xauusd",
            },
            clear=True,
        ):
            config = configured_symbol_config()

        self.assertEqual(config.symbols, ["EURUSD", "XAUUSD"])
        self.assertEqual(config.resolved_path, "ORACLE_SYMBOL")

    def test_configured_symbol_map_reads_general_mt5_env(self):
        with patch.dict(
            os.environ,
            {"MT5_SYMBOL_MAP_JSON": '{"EURUSD":"EURUSD.a","GBPJPY":"GBPJPY.m"}'},
            clear=True,
        ):
            mapping = configured_symbol_map_from_env()

        self.assertEqual(mapping, {"EURUSD": "EURUSD.a", "GBPJPY": "GBPJPY.m"})

    def test_broker_symbol_resolution_uses_wildcard_fallback(self):
        mt5 = _FakeMT5(
            successful_symbols={"GBPJPY.m"},
            wildcard_matches={"GBPJPY*": ["GBPJPY.m"]},
        )
        resolved = resolve_mt5_broker_symbol(mt5, "GBPJPY")

        self.assertEqual(resolved, "GBPJPY.m")
        self.assertIn("GBPJPY.m", mt5.selected_attempts)


if __name__ == "__main__":
    unittest.main()
