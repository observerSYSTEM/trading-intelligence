from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runner.providers.candle_provider_factory import get_candle_provider_from_env  # noqa: E402


def _candle_json(candle) -> dict:
    return {
        "symbol": candle.symbol,
        "timeframe": candle.timeframe,
        "time": candle.time_utc.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "source": candle.source,
        "complete": bool(candle.complete),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the latest closed candle from the configured API provider.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="M15")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        provider = get_candle_provider_from_env()
        candle = provider.get_latest_closed_candle(args.symbol, args.timeframe)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(_candle_json(candle), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
