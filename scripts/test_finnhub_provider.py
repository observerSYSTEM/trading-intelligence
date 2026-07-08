from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runner.providers.finnhub_provider import FinnhubProvider, FinnhubProviderError  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")

    provider = FinnhubProvider()
    from_date = date.today()
    to_date = from_date + timedelta(days=7)

    try:
        payload = {
            "provider": provider.name,
            "skipped_mt5": True,
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "economic_calendar": provider.get_economic_calendar(from_date, to_date),
            "market_news": provider.get_market_news(category="general"),
            "forex_news": provider.get_forex_news_or_general_news(),
        }
    except FinnhubProviderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
