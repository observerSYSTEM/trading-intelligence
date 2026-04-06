from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def normalize_database_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return value

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1].strip()

    # Render, Railway, and other hosts may provide postgres:// or postgresql:// URLs.
    if value.startswith("postgres://"):
        value = "postgresql+psycopg2://" + value[len("postgres://") :]
    elif value.startswith("postgresql://"):
        value = "postgresql+psycopg2://" + value[len("postgresql://") :]

    parsed = urlsplit(value)
    host = (parsed.hostname or "").strip()
    if host.startswith("<") and host.endswith(">"):
        raise ValueError(
            "DATABASE_URL still contains a placeholder host. Replace it with the real external hostname "
            "from Render and keep sslmode=require for local external connections."
        )

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if host.lower() not in {"", "localhost", "127.0.0.1"}:
        query_keys = {str(key).lower() for key in query}
        if "sslmode" not in query_keys:
            query["sslmode"] = "require"
            value = urlunsplit(parsed._replace(query=urlencode(query)))

    return value
