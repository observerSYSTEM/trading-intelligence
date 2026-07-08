from __future__ import annotations

import os
import logging
from datetime import date, datetime
from typing import Any

import requests


logger = logging.getLogger(__name__)


class FinnhubProviderError(RuntimeError):
    """Raised when Finnhub cannot return usable API data."""


class FinnhubProvider:
    name = "finnhub"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("FINNHUB_API_KEY") or "").strip()
        self.base_url = (base_url or os.getenv("FINNHUB_BASE_URL") or "https://finnhub.io/api/v1").rstrip("/")
        self.timeout_seconds = self._parse_timeout(timeout_seconds)
        self.session = session or requests.Session()

    @staticmethod
    def _parse_timeout(value: float | int | str | None) -> float:
        raw = value if value is not None else os.getenv("FINNHUB_TIMEOUT_SECONDS") or "10"
        try:
            return max(float(raw), 1.0)
        except (TypeError, ValueError):
            return 10.0

    @staticmethod
    def _format_date(value: date | datetime | str) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        formatted = str(value or "").strip()
        if not formatted:
            raise FinnhubProviderError("Finnhub date value cannot be empty.")
        return formatted

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise FinnhubProviderError("FINNHUB_API_KEY is required when DATA_PROVIDER=finnhub.")

    def _request(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._require_api_key()
        url = f"{self.base_url}/{path.lstrip('/')}"
        request_params = dict(params or {})
        request_params["token"] = self.api_key

        try:
            response = self.session.get(url, params=request_params, timeout=self.timeout_seconds)
        except requests.Timeout as exc:
            raise FinnhubProviderError(
                f"Finnhub request timed out after {self.timeout_seconds:g}s for {path}."
            ) from exc
        except requests.RequestException as exc:
            raise FinnhubProviderError(f"Finnhub request failed for {path}: {exc}") from exc

        if not response.ok:
            detail = response.text.strip()
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    detail = str(payload.get("error") or payload.get("message") or detail)
            except ValueError:
                pass
            if response.status_code == 403 and path.lstrip("/") == "calendar/economic":
                logger.warning(
                    "economic_calendar_unavailable provider=finnhub status_code=%s detail=%s",
                    response.status_code,
                    detail[:500],
                )
                return []
            raise FinnhubProviderError(
                f"Finnhub API request failed ({response.status_code}) for {path}: {detail[:500]}"
            )

        try:
            return response.json()
        except ValueError as exc:
            raise FinnhubProviderError(f"Finnhub API returned invalid JSON for {path}.") from exc

    def get_economic_calendar(self, from_date: date | datetime | str, to_date: date | datetime | str) -> Any:
        return self._request(
            "/calendar/economic",
            {
                "from": self._format_date(from_date),
                "to": self._format_date(to_date),
            },
        )

    def get_market_news(self, category: str = "general") -> Any:
        category_value = (category or "general").strip().lower() or "general"
        return self._request("/news", {"category": category_value})

    def get_forex_news_or_general_news(self, symbol: str | None = None) -> Any:
        forex_news = self.get_market_news(category="forex")
        if symbol and isinstance(forex_news, list):
            filtered = self._filter_news_by_symbol(forex_news, symbol)
            if filtered:
                return filtered
        if forex_news:
            return forex_news
        return self.get_market_news(category="general")

    @staticmethod
    def _filter_news_by_symbol(items: list[Any], symbol: str) -> list[Any]:
        terms = FinnhubProvider._symbol_terms(symbol)
        if not terms:
            return []
        filtered: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(
                str(item.get(key) or "")
                for key in ("headline", "summary", "related", "category", "source")
            ).upper()
            if any(term in haystack for term in terms):
                filtered.append(item)
        return filtered

    @staticmethod
    def _symbol_terms(symbol: str) -> set[str]:
        value = "".join(ch for ch in (symbol or "").upper() if ch.isalnum())
        if not value:
            return set()
        terms = {value}
        if len(value) == 6:
            base, quote = value[:3], value[3:]
            terms.update({base, quote, f"{base}/{quote}", f"{base}-{quote}"})
        if value.startswith("XAU"):
            terms.add("GOLD")
        return terms
