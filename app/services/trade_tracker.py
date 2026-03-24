from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.db.models import MT5Candle, Trade, TradeEvent
from app.services.trade_validation import validate_trade_payload


logger = logging.getLogger(__name__)


def _uk_tz():
    try:
        return ZoneInfo("Europe/London")
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401

            return ZoneInfo("Europe/London")
        except Exception:
            return timezone.utc


UK_TZ = _uk_tz()


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def to_uk_date(value: datetime) -> date:
    return as_utc(value).astimezone(UK_TZ).date()


def format_london(value: datetime) -> str:
    dt = as_utc(value).astimezone(UK_TZ)
    return dt.strftime("%Y-%m-%d %H:%M %Z")


def _latest_price(db: Session, symbol: str) -> float | None:
    row = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe.in_(["M1", "M5", "M15"]))
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )
    if not row:
        return None
    return float(row.close)


def _trade_no(db: Session, trade: Trade) -> int:
    rows = (
        db.query(Trade.id)
        .filter(Trade.user_id == trade.user_id, Trade.symbol == trade.symbol, Trade.date_uk == trade.date_uk)
        .order_by(Trade.opened_at.asc(), Trade.id.asc())
        .all()
    )
    ids = [str(r[0]) for r in rows]
    try:
        return ids.index(str(trade.id)) + 1
    except ValueError:
        return len(ids) + 1


def _event_exists(db: Session, trade_id, event_type: str) -> bool:
    existing = (
        db.query(TradeEvent.id)
        .filter(TradeEvent.trade_id == trade_id, TradeEvent.event_type == event_type)
        .first()
    )
    return existing is not None


def record_trade_event(db: Session, trade: Trade, event_type: str, price: float | None = None, note: str | None = None) -> TradeEvent | None:
    if _event_exists(db, trade.id, event_type):
        return None
    event = TradeEvent(
        trade_id=trade.id,
        user_id=trade.user_id,
        symbol=trade.symbol,
        event_type=event_type,
        tier_min=trade.tier,
        title=f"{trade.symbol} {event_type}",
        message=(note or "")[:4000] if note else None,
        meta_json={"trade_id": str(trade.id), "direction": trade.direction},
        price=price,
        note=note,
    )
    db.add(event)
    db.flush()
    return event


@dataclass
class TradeSignalPack:
    trade: Trade
    title: str
    body: str
    time_london: str
    date_uk: date


def create_trade_for_signal(
    db: Session,
    *,
    user_id,
    symbol: str,
    tier: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float | None,
    reasons: list[str],
    opened_at_utc: datetime,
    daily_permission: str | None = None,
    require_h1_confirmation: bool = False,
    h1_confirm_ok: bool | None = None,
    require_liquidity_context: bool = False,
    liquidity_context: dict | None = None,
    strategy_name: str | None = None,
) -> TradeSignalPack:
    opened = as_utc(opened_at_utc)
    validation = validate_trade_payload(
        direction=direction,
        entry=entry,
        sl=sl,
        tp=tp1,
        daily_permission=daily_permission,
        require_h1_confirmation=require_h1_confirmation,
        h1_confirm_ok=h1_confirm_ok,
        require_liquidity_context=require_liquidity_context,
        liquidity_context=liquidity_context,
    )
    if not validation.ok:
        raise ValueError(validation.reason)
    trade = Trade(
        user_id=user_id,
        symbol=symbol,
        date_uk=to_uk_date(opened),
        tier=tier,
        direction=direction,
        entry=entry,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        status="OPEN",
        opened_at=opened,
        reason_json={
            "reasons": reasons,
            "strategy_name": strategy_name,
            "validation_context": {
                "daily_permission": str(daily_permission or "").upper() or None,
                "require_h1_confirmation": bool(require_h1_confirmation),
                "h1_confirm_ok": h1_confirm_ok,
                "require_liquidity_context": bool(require_liquidity_context),
                "liquidity_context": liquidity_context if isinstance(liquidity_context, dict) else None,
            },
        },
    )
    db.add(trade)
    db.flush()
    record_trade_event(db, trade, "ENTRY", price=entry, note="Paper trade opened from oracle signal.")

    trade_no = _trade_no(db, trade)
    reason_1 = reasons[0] if reasons else "Directional alignment across monitored frames."
    reason_2 = reasons[1] if len(reasons) > 1 else "Execution focused on disciplined risk placement."
    body = (
        f"Trade #{trade_no} - ENTRY ({symbol})\n"
        f"Direction: {direction}\n"
        f"Entry: {entry:.2f}\n"
        f"SL: {sl:.2f}\n"
        f"TP: {tp1:.2f}\n"
        "Reason:\n"
        f"- {reason_1}\n"
        f"- {reason_2}\n"
        f"Time: {format_london(opened)}\n"
        f"ID: {trade.id}"
    )
    return TradeSignalPack(
        trade=trade,
        title=f"Trade #{trade_no} Entry",
        body=body,
        time_london=format_london(opened),
        date_uk=trade.date_uk,
    )


def _calc_rr(trade: Trade, exit_price: float) -> float:
    if trade.entry is None or trade.sl is None:
        return 0.0
    risk = abs(float(trade.entry) - float(trade.sl))
    if risk <= 0:
        return 0.0
    if trade.direction == "BUY":
        return (exit_price - float(trade.entry)) / risk
    return (float(trade.entry) - exit_price) / risk


def monitor_open_trades(db: Session) -> list[dict]:
    now_utc = datetime.now(timezone.utc)
    updates: list[dict] = []
    open_trades = db.query(Trade).filter(Trade.status.in_(["OPEN", "TP1"])).all()
    for trade in open_trades:
        price = _latest_price(db, trade.symbol)
        if price is None or trade.entry is None:
            continue

        reason_json = trade.reason_json if isinstance(trade.reason_json, dict) else {}
        validation_ctx = reason_json.get("validation_context") if isinstance(reason_json.get("validation_context"), dict) else {}
        validation = validate_trade_payload(
            direction=trade.direction,
            entry=trade.entry,
            sl=trade.sl,
            tp=trade.tp1,
            daily_permission=validation_ctx.get("daily_permission"),
            require_h1_confirmation=bool(validation_ctx.get("require_h1_confirmation")),
            h1_confirm_ok=validation_ctx.get("h1_confirm_ok"),
            require_liquidity_context=bool(validation_ctx.get("require_liquidity_context")),
            liquidity_context=validation_ctx.get("liquidity_context"),
        )
        if not validation.ok:
            logger.warning(
                "TRADE BLOCKED - %s trade_id=%s symbol=%s phase=trade_update",
                validation.reason,
                trade.id,
                trade.symbol,
            )
            continue

        trade_no = _trade_no(db, trade)
        reasons = reason_json.get("reasons", []) if isinstance(reason_json, dict) else []
        r1 = reasons[0] if reasons else "Directional bias remained aligned."
        r2 = reasons[1] if len(reasons) > 1 else "Risk execution stayed within plan."

        if trade.direction == "BUY":
            tp1_hit = trade.tp1 is not None and price >= float(trade.tp1)
            tp2_hit = trade.tp2 is not None and price >= float(trade.tp2)
            sl_hit = trade.sl is not None and price <= float(trade.sl)
        else:
            tp1_hit = trade.tp1 is not None and price <= float(trade.tp1)
            tp2_hit = trade.tp2 is not None and price <= float(trade.tp2)
            sl_hit = trade.sl is not None and price >= float(trade.sl)

        if tp1_hit and not _event_exists(db, trade.id, "TP1"):
            record_trade_event(db, trade, "TP1", price=float(trade.tp1), note="TP1 reached.")
            rr = _calc_rr(trade, float(trade.tp1))
            trade.status = "TP1"
            short_reason = r1
            body = (
                f"Trade #{trade_no} - UPDATE ({trade.symbol})\n"
                "Outcome: TP1\n"
                f"Entry: {float(trade.entry):.2f} -> TP1: {float(trade.tp1):.2f}\n"
                f"RR: +{rr:.2f}R\n"
                f"Timestamp: {format_london(now_utc)}\n"
                f"Reason: {short_reason}\n"
                f"ID: {trade.id}"
            )
            updates.append(
                {
                    "event_type": "TP1",
                    "trade_id": trade.id,
                    "user_id": trade.user_id,
                    "symbol": trade.symbol,
                    "date_uk": to_uk_date(now_utc),
                    "title": f"Trade #{trade_no} TP1",
                    "body": body,
                    "time_london": format_london(now_utc),
                }
            )
            if not _event_exists(db, trade.id, "BE"):
                record_trade_event(db, trade, "BE", price=float(trade.entry), note="Risk moved to breakeven zone.")

        if tp2_hit and not _event_exists(db, trade.id, "TP2"):
            record_trade_event(db, trade, "TP2", price=float(trade.tp2), note="TP2 reached.")
            if not _event_exists(db, trade.id, "CLOSE"):
                record_trade_event(db, trade, "CLOSE", price=float(trade.tp2), note="Trade closed at TP2.")
            trade.status = "TP2"
            trade.closed_at = now_utc
            trade.result = "WIN"
            trade.rr_realized = _calc_rr(trade, float(trade.tp2))
            short_reason = r1
            body = (
                f"Trade #{trade_no} - UPDATE ({trade.symbol})\n"
                "Outcome: TP2\n"
                f"Entry: {float(trade.entry):.2f} -> TP2: {float(trade.tp2):.2f}\n"
                f"RR: +{trade.rr_realized:.2f}R\n"
                f"Timestamp: {format_london(now_utc)}\n"
                f"Reason: {short_reason}\n"
                f"ID: {trade.id}"
            )
            updates.append(
                {
                    "event_type": "TP2",
                    "trade_id": trade.id,
                    "user_id": trade.user_id,
                    "symbol": trade.symbol,
                    "date_uk": to_uk_date(now_utc),
                    "title": f"Trade #{trade_no} TP2",
                    "body": body,
                    "time_london": format_london(now_utc),
                }
            )
            continue

        if sl_hit and not _event_exists(db, trade.id, "SL"):
            record_trade_event(db, trade, "SL", price=float(trade.sl), note="Stop-loss reached.")
            if not _event_exists(db, trade.id, "CLOSE"):
                record_trade_event(db, trade, "CLOSE", price=float(trade.sl), note="Trade closed at SL.")
            trade.status = "SL"
            trade.closed_at = now_utc
            trade.result = "LOSS"
            trade.rr_realized = -1.0
            short_reason = "Momentum failed and invalidated protected level."
            body = (
                f"Trade #{trade_no} - UPDATE ({trade.symbol})\n"
                "Outcome: SL\n"
                f"Entry: {float(trade.entry):.2f}\n"
                f"SL: {float(trade.sl):.2f}\n"
                "RR: -1R\n"
                f"Timestamp: {format_london(now_utc)}\n"
                f"Reason: {short_reason}\n"
                f"ID: {trade.id}"
            )
            updates.append(
                {
                    "event_type": "SL",
                    "trade_id": trade.id,
                    "user_id": trade.user_id,
                    "symbol": trade.symbol,
                    "date_uk": to_uk_date(now_utc),
                    "title": f"Trade #{trade_no} SL",
                    "body": body,
                    "time_london": format_london(now_utc),
                }
            )

    db.flush()
    return updates


def build_daily_audit_message(db: Session, *, user_id, symbol: str, date_uk: date, bias: str) -> str:
    trades = (
        db.query(Trade)
        .filter(Trade.user_id == user_id, Trade.symbol == symbol, Trade.date_uk == date_uk)
        .order_by(Trade.opened_at.asc())
        .all()
    )
    total_signals = len(trades)
    wins = len([t for t in trades if t.result == "WIN"])
    losses = len([t for t in trades if t.result == "LOSS"])
    be = len([t for t in trades if t.result == "BE"])
    tp_hits = (
        db.query(TradeEvent)
        .join(Trade, Trade.id == TradeEvent.trade_id)
        .filter(
            Trade.user_id == user_id,
            Trade.symbol == symbol,
            Trade.date_uk == date_uk,
            TradeEvent.event_type.in_(["TP1", "TP2"]),
        )
        .count()
    )
    sl_hits = (
        db.query(TradeEvent)
        .join(Trade, Trade.id == TradeEvent.trade_id)
        .filter(
            Trade.user_id == user_id,
            Trade.symbol == symbol,
            Trade.date_uk == date_uk,
            TradeEvent.event_type == "SL",
        )
        .count()
    )
    net_rr = round(sum(float(t.rr_realized or 0.0) for t in trades), 2)
    best_trade = None
    if trades:
        best_trade = max(trades, key=lambda t: float(t.rr_realized or -999.0))
    if best_trade and float(best_trade.rr_realized or -999.0) > -999.0:
        best_reasons = best_trade.reason_json.get("reasons", []) if isinstance(best_trade.reason_json, dict) else []
        best_setup_note = str(best_reasons[0]) if best_reasons else "Best setup had clean directional alignment."
    else:
        best_setup_note = "No closed setup to rank today."

    if total_signals == 0:
        improve = "Improve: Keep patience and wait for qualified structure."
    elif losses > wins:
        improve = "Improve: Reduce size during unstable follow-through and respect early invalidation."
    else:
        improve = "Improve: Keep execution disciplined and avoid unnecessary extra entries."

    return (
        f"Daily Audit - {symbol} ({date_uk.isoformat()})\n"
        f"Bias: {bias}\n"
        f"Trades: {total_signals}\n"
        f"Wins: {wins} | Losses: {losses} | BE: {be}\n"
        f"TP hits: {tp_hits} | SL hits: {sl_hits}\n"
        f"Net R: {net_rr}R\n"
        f"Best setup note: {best_setup_note}\n"
        f"{improve}"
    )
