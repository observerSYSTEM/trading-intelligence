from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_admin_token(*, api_base: str) -> str:
    direct = (os.getenv("ORACLE_EXEC_ADMIN_TOKEN") or "").strip()
    if direct:
        return direct

    email = (os.getenv("ADMIN_EMAIL") or "").strip()
    password = os.getenv("ADMIN_PASSWORD") or ""
    if not email or not password:
        raise RuntimeError("Set ORACLE_EXEC_ADMIN_TOKEN or ADMIN_EMAIL/ADMIN_PASSWORD in .env")

    response = requests.post(
        f"{api_base}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Admin login failed ({response.status_code}): {response.text}")
    payload = response.json()
    token = (payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Admin login did not return access_token")
    return token


def _fetch_instruction(*, api_base: str, token: str, payload: dict) -> dict:
    response = requests.post(
        f"{api_base}/admin/oracle/exec",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if response.status_code == 401:
        raise PermissionError("Unauthorized")
    if not response.ok:
        raise RuntimeError(f"Exec endpoint failed ({response.status_code}): {response.text}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Exec endpoint returned invalid JSON")
    return data


def _atomic_write_json(target_path: Path, payload: dict) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f"{target_path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def _build_payload() -> dict:
    symbol = (os.getenv("ORACLE_SYMBOL") or "XAUUSD").strip().upper()
    target_tier = (os.getenv("ORACLE_EXEC_TARGET_TIER") or "elite").strip().lower()
    session = (os.getenv("ORACLE_EXEC_SESSION") or "auto").strip().lower()
    ttl_raw = (os.getenv("ORACLE_EXEC_TTL_SECONDS") or "").strip()
    ttl_seconds = int(ttl_raw) if ttl_raw else None
    return {
        "symbol": symbol,
        "target_tier": target_tier,
        "session": session,
        "ttl_seconds": ttl_seconds,
    }


def main() -> int:
    load_dotenv()

    api_base = (os.getenv("API_BASE") or os.getenv("APP_URL") or "http://127.0.0.1:8000").rstrip("/")
    target_file_raw = (os.getenv("MT4_SIGNAL_FILE_PATH") or "").strip()
    if not target_file_raw:
        print("ERROR: MT4_SIGNAL_FILE_PATH is not set.", file=sys.stderr)
        return 2

    target_file = Path(target_file_raw)
    payload = _build_payload()
    interval_seconds = max(int(os.getenv("MT4_WRITER_INTERVAL_SECONDS") or "60"), 5)
    run_once = _as_bool(os.getenv("MT4_WRITER_ONCE"), default=False)

    token: str | None = None
    print(
        f"MT4 signal writer started. api_base={api_base} symbol={payload['symbol']} interval={interval_seconds}s target={target_file}",
        flush=True,
    )

    try:
        while True:
            loop_utc = datetime.now(timezone.utc)
            try:
                if not token:
                    token = _load_admin_token(api_base=api_base)

                try:
                    instruction = _fetch_instruction(api_base=api_base, token=token, payload=payload)
                except PermissionError:
                    token = _load_admin_token(api_base=api_base)
                    instruction = _fetch_instruction(api_base=api_base, token=token, payload=payload)

                instruction["writer"] = {
                    "source": "mt4_signal_writer",
                    "written_at_utc": loop_utc.isoformat(),
                }
                _atomic_write_json(target_file, instruction)

                print(
                    json.dumps(
                        {
                            "at": loop_utc.isoformat(),
                            "status": "ok",
                            "enabled": bool(instruction.get("enabled")),
                            "symbol": instruction.get("symbol"),
                            "side": instruction.get("side"),
                            "expires_at_utc": instruction.get("expires_at_utc"),
                            "file": str(target_file),
                        }
                    ),
                    flush=True,
                )
            except Exception as exc:
                print(
                    json.dumps(
                        {
                            "at": loop_utc.isoformat(),
                            "status": "error",
                            "error": str(exc),
                        }
                    ),
                    flush=True,
                )

            if run_once:
                break
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        print("MT4 signal writer stopped.", flush=True)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
