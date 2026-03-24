from __future__ import annotations

import os
import socket
from dataclasses import dataclass

from dotenv import load_dotenv


def _parse_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    values: list[str] = []
    for item in raw.split(","):
        value = item.strip().upper()
        if value and value not in values:
            values.append(value)
    return values


def _env_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RunnerSettings:
    api_base: str
    runner_api_key: str
    runner_id: str
    runner_version: str
    poll_interval_seconds: int
    heartbeat_interval_seconds: int
    positions_sync_interval_seconds: int
    request_timeout_seconds: int
    mt5_terminal_path: str | None
    mt5_login: int | None
    mt5_password: str | None
    mt5_server: str | None
    symbols_enabled: list[str]
    dry_run: bool

    @classmethod
    def from_env(cls) -> "RunnerSettings":
        load_dotenv()
        api_base = (os.getenv("API_BASE") or os.getenv("APP_URL") or "http://127.0.0.1:8000").strip().rstrip("/")
        runner_api_key = (os.getenv("RUNNER_API_KEY") or "").strip()
        runner_id = (os.getenv("RUNNER_ID") or socket.gethostname() or "mt5-runner").strip()
        runner_version = (os.getenv("RUNNER_VERSION") or "1.0.0").strip()
        poll_interval_seconds = int(os.getenv("RUNNER_JOB_POLL_SECONDS") or "5")
        heartbeat_interval_seconds = int(os.getenv("RUNNER_HEARTBEAT_SECONDS") or "30")
        positions_sync_interval_seconds = int(os.getenv("RUNNER_POS_SYNC_SECONDS") or "30")
        request_timeout_seconds = int(os.getenv("RUNNER_REQUEST_TIMEOUT_SECONDS") or "30")

        raw_symbols = os.getenv("RUNNER_SYMBOLS") or os.getenv("ORACLE_ENABLED_SYMBOLS")
        symbols_enabled = _parse_csv(raw_symbols) or ["XAUUSD"]

        mt5_terminal_path = (os.getenv("MT5_TERMINAL_PATH") or "").strip() or None
        mt5_login_raw = (os.getenv("MT5_LOGIN") or "").strip()
        mt5_login = int(mt5_login_raw) if mt5_login_raw else None
        mt5_password = (os.getenv("MT5_PASSWORD") or "").strip() or None
        mt5_server = (os.getenv("MT5_SERVER") or "").strip() or None
        dry_run = _env_bool(os.getenv("RUNNER_DRY_RUN"), default=False)

        settings = cls(
            api_base=api_base,
            runner_api_key=runner_api_key,
            runner_id=runner_id,
            runner_version=runner_version,
            poll_interval_seconds=max(poll_interval_seconds, 1),
            heartbeat_interval_seconds=max(heartbeat_interval_seconds, 10),
            positions_sync_interval_seconds=max(positions_sync_interval_seconds, 10),
            request_timeout_seconds=max(request_timeout_seconds, 5),
            mt5_terminal_path=mt5_terminal_path,
            mt5_login=mt5_login,
            mt5_password=mt5_password,
            mt5_server=mt5_server,
            symbols_enabled=symbols_enabled,
            dry_run=dry_run,
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.runner_api_key:
            raise RuntimeError("RUNNER_API_KEY is required on the runner machine.")
        if not self.api_base:
            raise RuntimeError("API_BASE is required.")
        if self.mt5_login is None:
            raise RuntimeError("MT5_LOGIN is required.")
        if not self.mt5_password:
            raise RuntimeError("MT5_PASSWORD is required.")
        if not self.mt5_server:
            raise RuntimeError("MT5_SERVER is required.")

