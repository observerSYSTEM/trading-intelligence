from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.core.symbols import normalize_plan
from app.db.models import NotificationRoute, Subscription, TradeEvent, User, UserSignalPref
from app.services.audit import log_audit
from app.services.oracle_snapshot import compute_dual_timeframe_snapshot
from app.services.strategy_matrix import DAILY_BIAS, StrategyMatrixError, ZONE_TO_ZONE, validate_symbol_for_strategy
from app.services.symbol_preferences import get_user_enabled_symbols
from app.services.telegram_service import send_thread_update
from app.services.trade_tracker import format_london, to_uk_date
from app.services.usage_service import UsageLimitExceeded, consume_usage, get_usage

TierName = Literal["basic", "pro", "elite"]
RunMode = Literal["daily_bias", "intraday_update"]

ACTIVE_SUB_STATUSES = {"active", "trialing"}
TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


def _normalize_symbol_list(values: list[str]) -> list[str]:
    output: list[str] = []
    for raw in values:
        symbol = (raw or "").strip().upper()
        if not symbol:
            continue
        if symbol not in output:
            output.append(symbol)
    return output


def _strategy_for_mode(mode: RunMode) -> str:
    return DAILY_BIAS if mode == "daily_bias" else ZONE_TO_ZONE


def _anchor_text(snapshot: dict, *, tier: str) -> str:
    symbol = str(snapshot.get("symbol", "XAUUSD"))
    final_basic = str(snapshot.get("final_allowed_basic", "NO_TRADE"))
    final_elite = str(snapshot.get("final_allowed_elite", final_basic))
    final_allowed = final_basic if tier == "basic" else final_elite
    confidence = float(snapshot.get("confidence") or 0.0)
    risk_banner = snapshot.get("risk_banner") if isinstance(snapshot.get("risk_banner"), dict) else {}
    weekly = snapshot.get("weekly_range") if isinstance(snapshot.get("weekly_range"), dict) else {}
    weekly_status = "Locked" if bool(weekly.get("range_ready")) else "Building"
    tier_copy = ""
    tier_copy_map = risk_banner.get("tier_copy") if isinstance(risk_banner.get("tier_copy"), dict) else {}
    if isinstance(tier_copy_map.get(tier), str):
        tier_copy = str(tier_copy_map.get(tier))

    lines = [
        f"DAILY BIAS - {symbol}",
        f"Bias: {final_allowed}",
        f"Confidence: {confidence * 100:.1f}%",
        f"Weekly Range: {weekly_status}",
    ]
    if tier_copy:
        lines.append(f"Risk Banner: {tier_copy}")
    lines.append("Thread: Daily intelligence updates.")
    return "\n".join(lines)


def _message_for_tier(snapshot: dict, *, tier: str, mode: RunMode) -> tuple[str, str]:
    symbol = str(snapshot.get("symbol", "XAUUSD"))
    final_basic = str(snapshot.get("final_allowed_basic", "NO_TRADE"))
    final_elite = str(snapshot.get("final_allowed_elite", final_basic))
    final_allowed = final_basic if tier == "basic" else final_elite
    confidence = float(snapshot.get("confidence") or 0.0)
    risk_banner = snapshot.get("risk_banner") if isinstance(snapshot.get("risk_banner"), dict) else {}
    weekly = snapshot.get("weekly_range") if isinstance(snapshot.get("weekly_range"), dict) else {}
    weekly_status = "Locked" if bool(weekly.get("range_ready")) else "Building"
    tier_copy_map = risk_banner.get("tier_copy") if isinstance(risk_banner.get("tier_copy"), dict) else {}
    tier_copy = str(tier_copy_map.get(tier, "Use standard risk controls."))

    title = "Daily Bias" if mode == "daily_bias" else "Intraday Update"
    if tier == "basic":
        body = (
            f"{symbol} Bias: {final_allowed}\n"
            f"Confidence: {confidence * 100:.1f}%\n"
            f"Risk Banner: {tier_copy}\n"
            f"Weekly Range: {weekly_status}"
        )
        return title, body

    if tier == "pro":
        liquidity = snapshot.get("next_liquidity_magnet")
        if liquidity is None:
            liquidity_text = "Liquidity update: not wired"
        else:
            liquidity_text = f"Liquidity update: {liquidity}"
        body = (
            f"{symbol} Bias: {final_allowed}\n"
            f"Confidence: {confidence * 100:.1f}%\n"
            f"{liquidity_text}\n"
            f"Risk Banner: {tier_copy}\n"
            f"Weekly Range: {weekly_status}"
        )
        return title, body

    confirmation = snapshot.get("internal", {}).get("confirmation", {}) if isinstance(snapshot.get("internal"), dict) else {}
    news_gate = bool(snapshot.get("news_gate_pass", True))
    vol_state = str(snapshot.get("volume_state", "normal"))
    manip_level = str(confirmation.get("manipulation_level", "unknown"))
    body = (
        f"{symbol} Bias: {final_allowed}\n"
        f"Confidence: {confidence * 100:.1f}%\n"
        f"Liquidity: {snapshot.get('next_liquidity_magnet', 'not wired')}\n"
        f"News Gate: {'PASS' if news_gate else 'BLOCKED'}\n"
        f"Volatility: {vol_state}\n"
        f"Manipulation: {manip_level}\n"
        f"Risk Banner: {tier_copy}\n"
        f"Weekly Range: {weekly_status}"
    )
    return title, body


def run_oracle_and_send(
    db: Session,
    *,
    symbols: list[str],
    tier_min: TierName,
    mode: RunMode,
    dry_run: bool,
    admin_user_id=None,
) -> dict:
    symbol_list = _normalize_symbol_list(symbols)
    if not symbol_list:
        raise ValueError("At least one symbol is required")

    tier_min_value = normalize_plan(tier_min)
    if tier_min_value not in TIER_ORDER:
        raise ValueError("Invalid tier_min")
    mode_value: RunMode = "intraday_update" if mode == "intraday_update" else "daily_bias"
    strategy_name = _strategy_for_mode(mode_value)

    recipients = (
        db.query(User, NotificationRoute, UserSignalPref, Subscription)
        .outerjoin(NotificationRoute, NotificationRoute.user_id == User.id)
        .outerjoin(UserSignalPref, UserSignalPref.user_id == User.id)
        .join(Subscription, Subscription.user_id == User.id)
        .filter(User.is_active.is_(True))
        .filter(Subscription.status.in_(ACTIVE_SUB_STATUSES))
        .all()
    )

    sent_count = 0
    skipped_count = 0
    blocked_count = 0
    skipped_reasons: Counter[str] = Counter()
    blocked_reasons: Counter[str] = Counter()
    symbol_reports: list[dict] = []
    dispatch_id = uuid.uuid4()

    for symbol in symbol_list:
        symbol_sent = 0
        symbol_skipped = 0
        symbol_blocked = 0
        symbol_note = "ok"
        snapshot: dict | None = None

        try:
            validate_symbol_for_strategy(symbol=symbol, strategy_name=strategy_name, tier=tier_min_value)
        except StrategyMatrixError as exc:
            reason = f"strategy_matrix_{exc.reason}"
            blocked_reasons[reason] += 1
            blocked_count += 1
            symbol_blocked += 1
            symbol_note = reason
            symbol_reports.append({"symbol": symbol, "sent": 0, "skipped": 0, "blocked": 1, "note": symbol_note})
            continue

        try:
            snapshot = compute_dual_timeframe_snapshot(db, symbol=symbol)
        except ValueError:
            blocked_reasons["snapshot_unavailable"] += 1
            blocked_count += 1
            symbol_blocked += 1
            symbol_note = "snapshot_unavailable"
            symbol_reports.append({"symbol": symbol, "sent": 0, "skipped": 0, "blocked": 1, "note": symbol_note})
            continue

        for user, route, pref, sub in recipients:
            pref_enabled = bool(pref.telegram_enabled) if pref else False
            pref_chat = (pref.telegram_chat_id or "").strip() if pref else ""
            route_enabled = bool(route.telegram_enabled) if route else False
            route_chat = (route.telegram_chat_id or "").strip() if route else ""
            enabled = pref_enabled or route_enabled
            chat_id = pref_chat or route_chat
            if not enabled or not chat_id:
                skipped_reasons["telegram_not_connected"] += 1
                skipped_count += 1
                symbol_skipped += 1
                continue

            if route is None:
                route = NotificationRoute(
                    user_id=user.id,
                    email_enabled=True,
                    telegram_enabled=enabled,
                    telegram_chat_id=chat_id,
                    telegram_pin_daily_bias=True,
                )
            else:
                route.telegram_enabled = enabled
                route.telegram_chat_id = chat_id

            plan = normalize_plan(sub.plan)
            if TIER_ORDER.get(plan, 0) < TIER_ORDER[tier_min_value]:
                skipped_reasons["tier_below_min"] += 1
                skipped_count += 1
                symbol_skipped += 1
                continue

            selected_symbols = get_user_enabled_symbols(db, user.id, plan)
            if symbol not in selected_symbols:
                skipped_reasons["symbol_not_enabled"] += 1
                skipped_count += 1
                symbol_skipped += 1
                continue

            try:
                validate_symbol_for_strategy(symbol=symbol, strategy_name=strategy_name, tier=plan)
            except StrategyMatrixError as exc:
                reason = f"strategy_matrix_{exc.reason}"
                blocked_reasons[reason] += 1
                blocked_count += 1
                symbol_blocked += 1
                continue

            usage_before = None
            if plan != "elite":
                usage_before = get_usage(db, user.id)
                limit = usage_before.get("limit")
                remaining = int(usage_before.get("remaining") or 0)
                if limit is not None and remaining <= 0:
                    skipped_reasons["usage_limit_exceeded"] += 1
                    skipped_count += 1
                    symbol_skipped += 1
                    continue

            if dry_run:
                skipped_reasons["dry_run"] += 1
                skipped_count += 1
                symbol_skipped += 1
                continue

            title, body = _message_for_tier(snapshot, tier=plan, mode=mode_value)
            now_utc = datetime.now(timezone.utc)
            try:
                update = send_thread_update(
                    db,
                    user_id=user.id,
                    chat_id=route.telegram_chat_id,
                    symbol=symbol,
                    date_uk=to_uk_date(now_utc),
                    title=title,
                    body=body,
                    time_london=format_london(now_utc),
                    pin_bool=bool(route.telegram_pin_daily_bias),
                    anchor_text=_anchor_text(snapshot, tier=plan),
                )
            except Exception as exc:
                blocked_reasons["telegram_send_failed"] += 1
                blocked_count += 1
                symbol_blocked += 1
                log_audit(
                    db,
                    action="admin.oracle.run_and_send.telegram_failed",
                    user_id=admin_user_id,
                    meta={"symbol": symbol, "target_user_id": str(user.id), "error": str(exc), "mode": mode_value},
                )
                db.commit()
                continue

            if plan != "elite":
                try:
                    consume_usage(
                        db,
                        user.id,
                        n=1,
                        reason=f"admin_oracle_run_and_send:{mode_value}",
                        symbol=symbol,
                        signal_id=f"admin-run-send:{dispatch_id}:{symbol}:{user.id}:{mode_value}",
                        meta={"mode": mode_value, "tier_min": tier_min_value, "message_id": update.get("message_id")},
                    )
                except UsageLimitExceeded:
                    blocked_reasons["usage_limit_exceeded_post_send"] += 1
                    blocked_count += 1
                    symbol_blocked += 1
                    db.commit()
                    continue

            sent_count += 1
            symbol_sent += 1
            db.add(
                TradeEvent(
                    trade_id=None,
                    user_id=user.id,
                    symbol=symbol,
                    event_type="SIGNAL",
                    tier_min=plan,
                    title=title,
                    message=body,
                    meta_json={
                        "mode": mode_value,
                        "tier_min": tier_min_value,
                        "strategy_name": strategy_name,
                        "dispatch_id": str(dispatch_id),
                        "message_id": update.get("message_id"),
                    },
                )
            )
            log_audit(
                db,
                action="admin.oracle.run_and_send.sent",
                user_id=admin_user_id,
                meta={
                    "symbol": symbol,
                    "target_user_id": str(user.id),
                    "target_tier": plan,
                    "mode": mode_value,
                    "tier_min": tier_min_value,
                    "usage_checked": usage_before is not None,
                },
            )
            db.commit()

        symbol_reports.append(
            {
                "symbol": symbol,
                "sent": symbol_sent,
                "skipped": symbol_skipped,
                "blocked": symbol_blocked,
                "note": symbol_note,
                "final_allowed_basic": snapshot.get("final_allowed_basic") if snapshot else None,
            }
        )

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "mode": mode_value,
        "tier_min": tier_min_value,
        "strategy_name": strategy_name,
        "symbols": symbol_list,
        "sent_count": sent_count,
        "skipped_count": skipped_count,
        "blocked_count": blocked_count,
        "skipped_reasons": dict(skipped_reasons),
        "blocked_reasons": dict(blocked_reasons),
        "symbol_reports": symbol_reports,
    }
