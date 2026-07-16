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

from app.api.oracle import _build_oracle_direction_payload, _latest_snapshot  # noqa: E402
from app.core.symbols import default_configured_symbol, normalize_symbol  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.services.liquidity_checkpoint_engine import get_liquidity_checkpoint  # noqa: E402
from app.services.observer_decision_engine import build_observer_decision, extract_daily_bias_from_snapshot  # noqa: E402
from app.services.observer_recommendation_engine import (  # noqa: E402
    build_observer_recommendation,
    format_observer_recommendation_card,
)


def _daily_bias(db, symbol: str, plan: str) -> str | None:
    return extract_daily_bias_from_snapshot(_latest_snapshot(db, symbol), plan=plan)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test Observer Recommendation Engine.")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--timeframe", default="H1")
    parser.add_argument("--lookback", type=int, default=100)
    parser.add_argument("--plan", default="elite")
    parser.add_argument("--card", action="store_true")
    args = parser.parse_args()

    symbol = normalize_symbol(args.symbol) or default_configured_symbol(args.plan)
    with SessionLocal() as db:
        lce = get_liquidity_checkpoint(db, symbol=symbol, timeframe=args.timeframe, lookback=args.lookback)
        oracle = _build_oracle_direction_payload(db, symbol=symbol, plan=args.plan)
        ode = build_observer_decision(
            symbol=symbol,
            timeframe=args.timeframe.strip().upper(),
            lce_result=lce,
            oracle_direction=oracle,
            daily_bias=_daily_bias(db, symbol, args.plan),
        )
        result = build_observer_recommendation(ode_result=ode)

    print(json.dumps(result, indent=2, default=str))
    if args.card:
        print()
        print(format_observer_recommendation_card(result))


if __name__ == "__main__":
    main()
