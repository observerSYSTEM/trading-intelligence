from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from app.db.session import SessionLocal  # noqa: E402
from app.services.liquidity_checkpoint_engine import (  # noqa: E402
    format_lce_telegram_card,
    get_liquidity_checkpoint,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test the Liquidity Checkpoint Engine.")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--telegram-card", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        result = get_liquidity_checkpoint(
            db,
            symbol=args.symbol,
            timeframe=args.timeframe,
            lookback=args.lookback,
        )

    print(json.dumps(result, indent=2, default=str))
    if args.telegram_card:
        print()
        print(format_lce_telegram_card(result))


if __name__ == "__main__":
    main()
