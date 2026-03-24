from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import requests

from runner.config import RunnerSettings
from runner.mt5_client import MT5Client, MT5OrderResult


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _headers(settings: RunnerSettings) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Runner-Key": settings.runner_api_key,
    }


def _http_post(session: requests.Session, settings: RunnerSettings, path: str, payload: dict) -> requests.Response:
    return session.post(
        f"{settings.api_base}{path}",
        headers=_headers(settings),
        json=payload,
        timeout=settings.request_timeout_seconds,
    )


def _http_get(session: requests.Session, settings: RunnerSettings, path: str, params: dict | None = None) -> requests.Response:
    return session.get(
        f"{settings.api_base}{path}",
        headers=_headers(settings),
        params=params,
        timeout=settings.request_timeout_seconds,
    )


def _parse_user_from_comment(comment: str) -> str | None:
    # Comments are best-effort hints. Full UUID is tracked in memory by ticket mapping.
    text = (comment or "").strip()
    if not text:
        return None
    if "u:" not in text:
        return None
    return None


def _emit(event: str, payload: dict) -> None:
    print(json.dumps({"event": event, **payload}), flush=True)


def _send_heartbeat(session: requests.Session, settings: RunnerSettings) -> None:
    payload = {
        "runner_id": settings.runner_id,
        "version": settings.runner_version,
        "symbols_enabled": settings.symbols_enabled,
    }
    response = _http_post(session, settings, "/runner/mt5/heartbeat", payload)
    if not response.ok:
        raise RuntimeError(f"heartbeat failed: {response.status_code} {response.text}")


def _fetch_next_job(session: requests.Session, settings: RunnerSettings) -> dict | None:
    response = _http_get(session, settings, "/runner/jobs/next", params={"runner_id": settings.runner_id})
    if response.status_code == 204:
        return None
    if not response.ok:
        raise RuntimeError(f"jobs/next failed: {response.status_code} {response.text}")
    data = response.json()
    if not isinstance(data, dict):
        return None
    return data.get("job")


def _send_job_result(
    session: requests.Session,
    settings: RunnerSettings,
    *,
    job_id: str,
    result: MT5OrderResult,
) -> None:
    payload = {
        "status": result.status,
        "broker_ticket": result.broker_ticket,
        "filled_price": result.filled_price,
        "error": result.error,
    }
    response = _http_post(session, settings, f"/runner/jobs/{job_id}/result", payload)
    if not response.ok:
        raise RuntimeError(f"job result failed job_id={job_id}: {response.status_code} {response.text}")


def _sync_positions(
    session: requests.Session,
    settings: RunnerSettings,
    mt5: MT5Client,
    *,
    ticket_meta: dict[str, dict],
    closed_events: list[dict],
) -> None:
    open_rows = mt5.get_open_positions(symbols=settings.symbols_enabled)
    rows: list[dict] = []

    for row in open_rows:
        ticket = str(row.get("ticket") or "").strip()
        if not ticket:
            continue
        meta = ticket_meta.get(ticket) or {}
        user_id = str(meta.get("user_id") or _parse_user_from_comment(str(row.get("comment") or "")) or "")
        if not user_id:
            continue
        ticket_meta[ticket] = {
            "user_id": user_id,
            "side": row.get("side") or meta.get("side"),
            "volume": row.get("volume") if row.get("volume") is not None else meta.get("volume"),
            "entry": row.get("entry") if row.get("entry") is not None else meta.get("entry"),
            "symbol": row.get("symbol") or meta.get("symbol"),
        }
        rows.append(
            {
                "user_id": user_id,
                "symbol": row["symbol"],
                "ticket": ticket,
                "side": str(row.get("side") or meta.get("side") or "BUY"),
                "volume": row["volume"],
                "entry": row["entry"],
                "sl": row.get("sl"),
                "tp": row.get("tp"),
                "pnl": row.get("pnl"),
                "status": "OPEN",
                "reason": None,
            }
        )

    for row in closed_events:
        ticket = str(row.get("ticket") or "").strip()
        if not ticket:
            continue
        meta = ticket_meta.get(ticket)
        if not meta:
            continue
        user_id = str(meta.get("user_id") or "")
        if not user_id:
            continue
        rows.append(
            {
                "user_id": user_id,
                "symbol": row.get("symbol") or meta.get("symbol") or "",
                "ticket": ticket,
                "side": str(meta.get("side") or "BUY"),
                "volume": float(meta.get("volume") or 0.01),
                "entry": float(meta.get("entry") or row.get("price") or 0.0),
                "status": row.get("status") or "CLOSED",
                "reason": row.get("reason"),
                "price": row.get("price"),
                "sl": None,
                "tp": None,
                "pnl": None,
            }
        )
        ticket_meta.pop(ticket, None)

    if not rows:
        return

    payload = {
        "runner_id": settings.runner_id,
        "positions": rows,
    }
    response = _http_post(session, settings, "/runner/positions/sync", payload)
    if not response.ok:
        raise RuntimeError(f"positions/sync failed: {response.status_code} {response.text}")


def main() -> int:
    settings = RunnerSettings.from_env()
    mt5 = MT5Client(settings)
    session = requests.Session()

    heartbeat_due = _now_utc()
    sync_due = _now_utc()
    last_closed_scan = _now_utc() - timedelta(minutes=10)
    ticket_meta: dict[str, dict] = {}

    _emit(
        "runner_started",
        {
            "runner_id": settings.runner_id,
            "api_base": settings.api_base,
            "symbols_enabled": settings.symbols_enabled,
            "dry_run": settings.dry_run,
        },
    )

    try:
        while True:
            now_utc = _now_utc()
            if now_utc >= heartbeat_due:
                try:
                    _send_heartbeat(session, settings)
                    _emit("heartbeat_ok", {"at": now_utc.isoformat()})
                except Exception as exc:
                    _emit("heartbeat_failed", {"at": now_utc.isoformat(), "error": str(exc)})
                heartbeat_due = now_utc + timedelta(seconds=settings.heartbeat_interval_seconds)

            try:
                job = _fetch_next_job(session, settings)
            except Exception as exc:
                _emit("poll_failed", {"at": now_utc.isoformat(), "error": str(exc)})
                time.sleep(settings.poll_interval_seconds)
                continue

            if job:
                job_id = str(job.get("id"))
                user_id = str(job.get("user_id") or "")
                _emit("job_received", {"job_id": job_id, "symbol": job.get("symbol"), "side": job.get("side")})
                try:
                    result = mt5.execute_job(job)
                    _send_job_result(session, settings, job_id=job_id, result=result)
                    _emit(
                        "job_result_sent",
                        {
                            "job_id": job_id,
                            "status": result.status,
                            "ticket": result.broker_ticket,
                            "filled_price": result.filled_price,
                            "error": result.error,
                        },
                    )
                    if result.status == "filled" and result.broker_ticket and user_id:
                        ticket_meta[str(result.broker_ticket)] = {
                            "user_id": user_id,
                            "side": str(job.get("side") or ""),
                            "volume": float(job.get("volume") or 0.0),
                            "entry": float(result.filled_price if result.filled_price is not None else (job.get("entry_price") or 0.0)),
                            "symbol": str(job.get("symbol") or ""),
                        }
                except Exception as exc:
                    _emit("job_execution_failed", {"job_id": job_id, "error": str(exc)})
                    fallback = MT5OrderResult(status="failed", error=str(exc))
                    try:
                        _send_job_result(session, settings, job_id=job_id, result=fallback)
                    except Exception as result_exc:
                        _emit("job_result_send_failed", {"job_id": job_id, "error": str(result_exc)})

            if now_utc >= sync_due:
                try:
                    closed_events = mt5.get_recent_closed_events(since_utc=last_closed_scan)
                    _sync_positions(
                        session,
                        settings,
                        mt5,
                        ticket_meta=ticket_meta,
                        closed_events=closed_events,
                    )
                    last_closed_scan = now_utc
                    _emit("positions_sync_ok", {"at": now_utc.isoformat(), "closed_events": len(closed_events)})
                except Exception as exc:
                    _emit("positions_sync_failed", {"at": now_utc.isoformat(), "error": str(exc)})
                sync_due = now_utc + timedelta(seconds=settings.positions_sync_interval_seconds)

            time.sleep(settings.poll_interval_seconds)
    except KeyboardInterrupt:
        _emit("runner_stopped", {"at": _now_utc().isoformat()})
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
