from __future__ import annotations


def normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return value

    # Railway and other hosts may provide postgres:// URLs.
    if value.startswith("postgres://"):
        return "postgresql://" + value[len("postgres://") :]
    return value

