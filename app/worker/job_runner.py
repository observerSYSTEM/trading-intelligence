from __future__ import annotations

import argparse
import json
import logging

from dotenv import load_dotenv

load_dotenv()

from app.services.oracle_scheduler import (
    run_daily_audit_now,
    run_daily_permission_all_symbols_job,
    run_m15_opportunity_all_symbols_job,
    run_oracle_all_symbols_job,
    run_prelim_permission_all_symbols_job,
    run_targets_h1_refresh_job,
)

logger = logging.getLogger("app.worker.job_runner")


def _run_hourly() -> dict:
    oracle = run_oracle_all_symbols_job()
    targets = run_targets_h1_refresh_job()
    return {"oracle_hourly": oracle, "targets_refresh": targets}


def _run_m15() -> dict:
    return {"m15_opportunity": run_m15_opportunity_all_symbols_job()}


def _run_permissions() -> dict:
    prelim = run_prelim_permission_all_symbols_job()
    official = run_daily_permission_all_symbols_job()
    return {"daily_permission_prelim": prelim, "daily_permission_official": official}


def _run_eod() -> dict:
    return {"daily_audit": run_daily_audit_now()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Oracle cron-compatible job runner")
    parser.add_argument(
        "--job",
        choices=["hourly", "m15", "permission", "eod", "all"],
        default="all",
        help="Job group to run once.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger.info("Starting job runner job=%s", args.job)

    if args.job == "hourly":
        result = _run_hourly()
    elif args.job == "m15":
        result = _run_m15()
    elif args.job == "permission":
        result = _run_permissions()
    elif args.job == "eod":
        result = _run_eod()
    else:
        result = {
            **_run_permissions(),
            **_run_hourly(),
            **_run_m15(),
        }

    print(json.dumps({"ok": True, "job": args.job, "result": result}, default=str))


if __name__ == "__main__":
    main()

