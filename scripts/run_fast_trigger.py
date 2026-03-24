from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from dotenv import load_dotenv


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, payload: dict, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)

    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    load_dotenv()

    app_url = os.getenv("APP_URL", "http://127.0.0.1:8000").rstrip("/")
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_password = os.getenv("ADMIN_PASSWORD")
    symbol = os.getenv("ORACLE_SYMBOL", "XAUUSD")

    if not admin_email or not admin_password:
        print("ERROR: ADMIN_EMAIL and ADMIN_PASSWORD must be set in environment.", file=sys.stderr)
        return 2

    token_url = f"{app_url}/auth/token"
    run_url = f"{app_url}/admin/oracle/run-and-send"

    try:
        token_data = _post_form(
            token_url,
            {"username": admin_email, "password": admin_password},
        )
        token = token_data.get("access_token")
        if not token:
            print(f"ERROR: access_token missing from /auth/token response: {token_data}", file=sys.stderr)
            return 3

        result = _post_json(
            run_url,
            payload={"symbol": symbol},
            headers={"Authorization": f"Bearer {token}"},
        )
        print(json.dumps(result, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
