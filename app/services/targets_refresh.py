from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.symbols import enabled_symbols_from_settings
from app.core.time_utils import LONDON_TZ, LONDON_TZ_AVAILABLE
from app.db.models import MT5Candle, MT5IngestStatus, OracleMagnetState, OracleTargetsSnapshot
from app.services.data_provider import api_candle_mode, get_data_provider
from app.services.telegram_alerts import latest_oracle_alert_context, maybe_send_liquidity_target_alert

logger = logging.getLogger(__name__)

EPS = 1e-9
BROKER_OFFSET_CACHE_SECONDS: dict[str, int] = {}
TARGETS_MARKET_FEED_DELAY_THRESHOLD_SECONDS = 10 * 60


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _anchor_timeframe_delta(timeframe: str) -> timedelta:
    mapping = {
        "M1": timedelta(minutes=1),
        "M5": timedelta(minutes=5),
        "M15": timedelta(minutes=15),
    }
    return mapping[timeframe.strip().upper()]


def _complete_candles(candles: list) -> list:
    return [candle for candle in candles if bool(getattr(candle, "complete", True))]


def _candle_contains_target(candle, *, timeframe: str, target_utc: datetime) -> bool:
    candle_start = _as_utc(candle.time_utc)
    return candle_start <= target_utc < candle_start + _anchor_timeframe_delta(timeframe)


def get_cached_broker_offset_seconds(symbol: str) -> int | None:
    return BROKER_OFFSET_CACHE_SECONDS.get(symbol.strip().upper())


def _latest_candle(db: Session, *, symbol: str, timeframe: str) -> MT5Candle | None:
    return (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )


def _latest_candles(db: Session, *, symbol: str, timeframe: str, limit: int) -> list[MT5Candle]:
    rows = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return rows


def latest_market_feed_freshness(
    db: Session,
    *,
    symbol: str,
    delay_threshold_seconds: int = TARGETS_MARKET_FEED_DELAY_THRESHOLD_SECONDS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    symbol_value = symbol.strip().upper()
    current_utc = _as_utc(now_utc or datetime.now(timezone.utc))
    threshold_seconds = max(int(delay_threshold_seconds), 30)

    ingest_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol_value).first()
    latest_candle = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol_value)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )

    last_ingest_at = _as_utc(ingest_row.last_ingested_at) if ingest_row else None
    latest_candle_time = _as_utc(latest_candle.time_utc) if latest_candle else None
    latest_candle_timeframe = str(latest_candle.timeframe).upper() if latest_candle and latest_candle.timeframe else None
    api_mode = api_candle_mode()
    ingest_updated_at = (
        _as_utc(ingest_row.updated_at)
        if api_mode and ingest_row is not None and getattr(ingest_row, "updated_at", None) is not None
        else None
    )

    latest_market_feed_at: datetime | None = None
    latest_market_feed_source: str | None = None
    if api_mode:
        candidates: list[tuple[datetime, str]] = []
        if ingest_updated_at:
            candidates.append((ingest_updated_at, "ingest_status_updated_at"))
        if last_ingest_at:
            candidates.append((last_ingest_at, "ingest_status"))
        if latest_candle_time:
            candidates.append((latest_candle_time, f"latest_candle:{latest_candle_timeframe or 'UNKNOWN'}"))
        if candidates:
            latest_market_feed_at, latest_market_feed_source = max(candidates, key=lambda item: item[0])
    elif last_ingest_at and latest_candle_time:
        if last_ingest_at >= latest_candle_time:
            latest_market_feed_at = last_ingest_at
            latest_market_feed_source = "ingest_status"
        else:
            latest_market_feed_at = latest_candle_time
            latest_market_feed_source = f"latest_candle:{latest_candle_timeframe or 'UNKNOWN'}"
    elif last_ingest_at:
        latest_market_feed_at = last_ingest_at
        latest_market_feed_source = "ingest_status"
    elif latest_candle_time:
        latest_market_feed_at = latest_candle_time
        latest_market_feed_source = f"latest_candle:{latest_candle_timeframe or 'UNKNOWN'}"

    market_feed_age_seconds = (
        max(int((current_utc - latest_market_feed_at).total_seconds()), 0)
        if latest_market_feed_at is not None
        else None
    )
    market_feed_delayed = (
        True
        if latest_market_feed_at is None
        else bool(market_feed_age_seconds is not None and market_feed_age_seconds > threshold_seconds)
    )
    if latest_market_feed_at is None:
        market_feed_delay_reason = "missing_market_feed"
    elif market_feed_delayed:
        market_feed_delay_reason = "freshness_threshold_exceeded"
    else:
        market_feed_delay_reason = None

    return {
        "symbol": symbol_value,
        "last_ingest_at": last_ingest_at,
        "ingest_updated_at": ingest_updated_at,
        "latest_candle_time": latest_candle_time,
        "latest_candle_timeframe": latest_candle_timeframe,
        "latest_market_feed_at": latest_market_feed_at,
        "latest_market_feed_source": latest_market_feed_source,
        "market_feed_age_seconds": market_feed_age_seconds,
        "market_feed_delayed": market_feed_delayed,
        "market_feed_delay_reason": market_feed_delay_reason,
        "market_feed_delay_threshold_seconds": threshold_seconds,
    }


def _atr_from_h1(candles: list[MT5Candle], period: int = 14) -> float | None:
    if len(candles) < period + 1:
        return None
    trs: list[float] = []
    for idx in range(1, len(candles)):
        c = candles[idx]
        p = candles[idx - 1]
        high = float(c.high)
        low = float(c.low)
        prev_close = float(p.close)
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    window = trs[-period:]
    if not window:
        return None
    return sum(window) / len(window)


def _latest_targets_row(db: Session, *, symbol: str, tier: str) -> OracleTargetsSnapshot | None:
    row = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == tier)
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .first()
    )
    if row:
        return row
    return (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol)
        .order_by(OracleTargetsSnapshot.as_of_utc.desc(), OracleTargetsSnapshot.created_at.desc())
        .first()
    )


def _upsert_candle(
    db: Session,
    *,
    symbol: str,
    timeframe: str,
    candle_time_utc: datetime,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None,
) -> bool:
    row = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == timeframe,
            MT5Candle.time_utc == candle_time_utc,
        )
        .first()
    )
    created = row is None
    if row is None:
        row = MT5Candle(symbol=symbol, timeframe=timeframe, time_utc=candle_time_utc)
        db.add(row)
    row.open = float(open_)
    row.high = float(high)
    row.low = float(low)
    row.close = float(close)
    row.volume = _safe_float(volume)
    return created


def backfill_london_open_m1_window(
    db: Session,
    *,
    symbol: str,
    date_uk: date,
    minutes_before: int = 5,
    minutes_after: int = 6,
) -> dict[str, Any]:
    symbol_value = symbol.strip().upper()
    if not LONDON_TZ_AVAILABLE:
        return {
            "ok": False,
            "symbol": symbol_value,
            "date_uk": date_uk.isoformat(),
            "error": "timezone_unavailable",
            "timezone": "UTC_FALLBACK",
            "found_0801": False,
            "created": 0,
            "updated": 0,
        }

    provider = get_data_provider()
    provider_primary = getattr(getattr(provider, "primary", None), "name", getattr(provider, "name", None))
    provider_fallback = getattr(getattr(provider, "fallback", None), "name", None)
    api_mode = api_candle_mode()
    anchor_provider = provider
    primary_provider = getattr(provider, "primary", None)
    if api_mode and primary_provider is not None and str(getattr(primary_provider, "name", "") or "").strip().lower() == "oanda":
        anchor_provider = primary_provider
    window_start_local = datetime.combine(
        date_uk,
        time(hour=8, minute=1, tzinfo=LONDON_TZ),
    ) - timedelta(minutes=max(int(minutes_before), 0))
    window_end_local = datetime.combine(
        date_uk,
        time(hour=8, minute=1, tzinfo=LONDON_TZ),
    ) + timedelta(minutes=max(int(minutes_after), 0) + 1)
    window_start_utc = _as_utc(window_start_local)
    window_end_utc = _as_utc(window_end_local)
    target_0801_utc = _as_utc(datetime.combine(date_uk, time(hour=8, minute=1, tzinfo=LONDON_TZ)))

    anchor_timeframe = "M1"
    anchor_candle_status: str | None = None
    m1_candles_returned = 0
    fetch_error: str | None = None
    try:
        candles = _complete_candles(
            anchor_provider.get_candles_range(
                symbol=symbol_value,
                timeframe="M1",
                start_utc=window_start_utc,
                end_utc=window_end_utc,
            )
        )
        m1_candles_returned = len(candles)
    except Exception as exc:
        fetch_error = str(exc)
        if not api_mode:
            logger.exception(
                "08:01 backfill failed symbol=%s date_uk=%s start=%s end=%s",
                symbol_value,
                date_uk.isoformat(),
                window_start_utc.isoformat(),
                window_end_utc.isoformat(),
            )
            return {
                "ok": False,
                "symbol": symbol_value,
                "date_uk": date_uk.isoformat(),
                "error": str(exc),
                "window_start_utc": window_start_utc.isoformat(),
                "window_end_utc": window_end_utc.isoformat(),
                "target_0801_utc": target_0801_utc.isoformat(),
                "candle_provider": provider_primary,
                "fallback_provider": provider_fallback,
                "anchor_candle_source": None,
                "anchor_candle_status": "fetch_failed",
                "found_0801": False,
                "created": 0,
                "updated": 0,
            }
        candles = []
        logger.warning(
            "api_anchor_lookup_m1_failed symbol=%s target_utc=%s provider=%s error=%s",
            symbol_value,
            target_0801_utc.isoformat(),
            provider_primary,
            exc,
        )

    if api_mode:
        exact_m1 = next((candle for candle in candles if _as_utc(candle.time_utc) == target_0801_utc), None)
        if exact_m1 is None:
            if fetch_error is None:
                logger.warning(
                    "api_anchor_lookup_m1_failed symbol=%s target_utc=%s count=%s provider=%s",
                    symbol_value,
                    target_0801_utc.isoformat(),
                    m1_candles_returned,
                    provider_primary,
                )
            fallback_selected = None
            fallback_candles: list = []
            for timeframe in ("M5", "M15"):
                try:
                    delta = _anchor_timeframe_delta(timeframe)
                    candidate_candles = _complete_candles(
                        anchor_provider.get_candles_range(
                            symbol=symbol_value,
                            timeframe=timeframe,
                            start_utc=target_0801_utc - delta,
                            end_utc=target_0801_utc + timedelta(seconds=1),
                        )
                    )
                    candidate = next(
                        (
                            candle
                            for candle in candidate_candles
                            if _candle_contains_target(candle, timeframe=timeframe, target_utc=target_0801_utc)
                        ),
                        None,
                    )
                    if candidate is None:
                        continue
                    event_name = f"api_anchor_lookup_{timeframe.lower()}_fallback_ok"
                    logger.info(
                        "%s symbol=%s target_utc=%s candle_time_utc=%s provider=%s source=%s",
                        event_name,
                        symbol_value,
                        target_0801_utc.isoformat(),
                        _as_utc(candidate.time_utc).isoformat(),
                        provider_primary,
                        getattr(candidate, "source", None),
                    )
                    fallback_selected = candidate
                    fallback_candles = candidate_candles
                    anchor_timeframe = timeframe
                    anchor_candle_status = f"{timeframe.lower()}_fallback_ok"
                    break
                except Exception:
                    logger.exception(
                        "08:01 API anchor fallback failed symbol=%s timeframe=%s target_utc=%s",
                        symbol_value,
                        timeframe,
                        target_0801_utc.isoformat(),
                    )
            if fallback_selected is not None:
                candles = fallback_candles
        else:
            anchor_candle_status = "found"
    if api_mode and not candles and fetch_error and anchor_candle_status is None:
        anchor_candle_status = "fetch_failed"

    created = 0
    updated = 0
    latest_ingested_at: datetime | None = None
    found_0801 = False
    nearest_candle = None
    if candles:
        nearest_candle = min(
            candles,
            key=lambda candle: (abs((_as_utc(candle.time_utc) - target_0801_utc).total_seconds()), _as_utc(candle.time_utc)),
        )
    resolved_candle_symbol = (
        str(candles[0].broker_symbol).strip()
        if candles and getattr(candles[0], "broker_symbol", None)
        else symbol_value
    )
    anchor_candle_source = str(getattr(candles[0], "source", "") or provider_primary or "").strip() if candles else None
    for candle in candles:
        candle_time = _as_utc(candle.time_utc)
        was_created = _upsert_candle(
            db,
            symbol=symbol_value,
            timeframe=anchor_timeframe,
            candle_time_utc=candle_time,
            open_=float(candle.open),
            high=float(candle.high),
            low=float(candle.low),
            close=float(candle.close),
            volume=candle.volume,
        )
        if was_created:
            created += 1
        else:
            updated += 1
        latest_ingested_at = candle_time if latest_ingested_at is None else max(latest_ingested_at, candle_time)
        if candle_time == target_0801_utc or (
            api_mode and _candle_contains_target(candle, timeframe=anchor_timeframe, target_utc=target_0801_utc)
        ):
            found_0801 = True

    anchor_candle_status = anchor_candle_status or (
        "found" if found_0801 else ("nearest_available" if nearest_candle is not None else "missing")
    )

    if latest_ingested_at is not None:
        status_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol_value).first()
        if not status_row:
            status_row = MT5IngestStatus(symbol=symbol_value, last_ingested_at=latest_ingested_at)
            db.add(status_row)
        elif _as_utc(status_row.last_ingested_at) < latest_ingested_at:
            status_row.last_ingested_at = latest_ingested_at
            db.add(status_row)

    logger.info(
        "08:01 backfill symbol=%s resolved_symbol=%s date_uk=%s candles=%s created=%s updated=%s found_0801=%s",
        symbol_value,
        resolved_candle_symbol,
        date_uk.isoformat(),
        len(candles),
        created,
        updated,
        found_0801,
    )
    return {
        "ok": True,
        "symbol": symbol_value,
        "requested_symbol": symbol_value,
        "resolved_mt5_symbol": resolved_candle_symbol,
        "resolved_candle_symbol": resolved_candle_symbol,
        "candle_provider": provider_primary,
        "fallback_provider": provider_fallback,
        "anchor_candle_source": anchor_candle_source,
        "anchor_candle_status": anchor_candle_status,
        "date_uk": date_uk.isoformat(),
        "window_start_utc": window_start_utc.isoformat(),
        "window_end_utc": window_end_utc.isoformat(),
        "target_0801_utc": target_0801_utc.isoformat(),
        "anchor_timeframe": anchor_timeframe,
        "fallback_timeframe": anchor_timeframe if anchor_timeframe != "M1" else None,
        "candles": len(candles),
        "m1_candles_returned": m1_candles_returned,
        "created": created,
        "updated": updated,
        "found_0801": found_0801,
        "nearest_available_candle_time": _as_utc(nearest_candle.time_utc).isoformat() if nearest_candle is not None else None,
        "nearest_available_candle_delta_seconds": (
            int(abs((_as_utc(nearest_candle.time_utc) - target_0801_utc).total_seconds()))
            if nearest_candle is not None
            else None
        ),
    }


def _upsert_magnet_state(
    db: Session,
    *,
    symbol: str,
    timeframe_base: str,
    as_of_utc: datetime,
    magnet_price: float,
    magnet_side: str,
    zone_to_zone_target: float,
    sellside_liquidity: float,
    buyside_liquidity: float,
    state_json: dict[str, Any],
) -> OracleMagnetState:
    row = db.query(OracleMagnetState).filter(OracleMagnetState.symbol == symbol).first()
    if row is None:
        row = OracleMagnetState(symbol=symbol)
        db.add(row)

    row.timeframe_base = timeframe_base
    row.as_of_utc = _as_utc(as_of_utc)
    row.magnet_price = float(magnet_price)
    row.magnet_side = str(magnet_side).upper()
    row.zone_to_zone_target = float(zone_to_zone_target)
    row.sellside_liquidity = float(sellside_liquidity)
    row.buyside_liquidity = float(buyside_liquidity)
    row.state_json = state_json
    db.flush()
    return row


def latest_magnet_state(db: Session, *, symbol: str) -> OracleMagnetState | None:
    return db.query(OracleMagnetState).filter(OracleMagnetState.symbol == symbol.strip().upper()).first()


def _magnet_alert_threshold_for_symbol(symbol: str) -> float:
    sym = (symbol or "").strip().upper()
    if sym == "XAUUSD":
        return 0.30
    if sym == "BTCUSD":
        return 30.0
    if sym == "GBPJPY":
        return 0.08
    if sym in {"GBPUSD", "EURUSD"}:
        return 0.0005
    return 0.05


def _liquidity_levels_changed_meaningfully(
    *,
    symbol: str,
    latest: OracleTargetsSnapshot | None,
    levels: dict[str, Any],
) -> bool:
    if latest is None:
        return False

    latest_state = latest.magnet_state if isinstance(latest.magnet_state, dict) else {}
    latest_current = latest_state.get("current") if isinstance(latest_state.get("current"), dict) else {}
    before_side = str(latest_current.get("side") or "").strip().upper()
    after_side = str(levels.get("magnet_side") or "").strip().upper()
    if before_side and after_side and before_side != after_side:
        return True

    threshold = _magnet_alert_threshold_for_symbol(symbol)
    before_magnet = _safe_float(latest.magnet_price)
    after_magnet = _safe_float(levels.get("magnet_price"))
    if before_magnet is None or after_magnet is None:
        if before_magnet != after_magnet:
            return True
    elif abs(after_magnet - before_magnet) >= threshold:
        return True

    before_target = _safe_float(latest.zone_to_zone_target)
    after_target = _safe_float(levels.get("zone_to_zone_target"))
    if before_target is None or after_target is None:
        return before_target != after_target

    return abs(after_target - before_target) >= threshold


def detect_magnet_hit(
    *,
    magnet_side: str,
    magnet_price: float,
    bid: float,
    ask: float,
    atr_h1: float | None = None,
    m1_close: float | None = None,
) -> dict[str, Any]:
    side = (magnet_side or "").strip().upper()
    spread = max(float(ask) - float(bid), EPS)
    tolerance = max(spread * 1.5, 0.05 * float(atr_h1)) if atr_h1 and atr_h1 > 0 else (spread * 1.5)

    touched = False
    if side == "BUY":
        touched = float(ask) >= (float(magnet_price) - tolerance)
        close_confirm = (m1_close is not None) and (float(m1_close) >= float(magnet_price))
        hit_price = float(ask)
    elif side == "SELL":
        touched = float(bid) <= (float(magnet_price) + tolerance)
        close_confirm = (m1_close is not None) and (float(m1_close) <= float(magnet_price))
        hit_price = float(bid)
    else:
        return {
            "hit": False,
            "side": side,
            "tolerance": tolerance,
            "reason": "invalid_magnet_side",
            "confidence": "none",
            "hit_price": None,
        }

    if not touched:
        return {
            "hit": False,
            "side": side,
            "tolerance": tolerance,
            "reason": "not_touched",
            "confidence": "none",
            "hit_price": None,
        }

    confidence = "confirmed" if close_confirm else "lower"
    return {
        "hit": True,
        "side": side,
        "tolerance": tolerance,
        "reason": "touched_and_confirmed" if close_confirm else "single_touch",
        "confidence": confidence,
        "hit_price": hit_price,
    }


def _compute_levels(
    db: Session,
    *,
    symbol: str,
    price_mid: float,
    prefer_side: str | None = None,
) -> dict[str, Any]:
    h1 = _latest_candles(db, symbol=symbol, timeframe="H1", limit=48)
    if len(h1) < 2:
        raise ValueError(f"No closed H1 candles available for {symbol}")

    lookback = h1[-20:] if len(h1) >= 20 else h1
    buyside_liquidity = max(float(c.high) for c in lookback)
    sellside_liquidity = min(float(c.low) for c in lookback)
    atr_h1 = _atr_from_h1(h1, period=14)
    last_h1 = h1[-1]

    side_pref = (prefer_side or "").strip().upper()
    if side_pref in {"BUY", "SELL"}:
        magnet_side = side_pref
    else:
        dist_buy = abs(float(buyside_liquidity) - float(price_mid))
        dist_sell = abs(float(price_mid) - float(sellside_liquidity))
        magnet_side = "BUY" if dist_buy <= dist_sell else "SELL"

    magnet_price = float(buyside_liquidity) if magnet_side == "BUY" else float(sellside_liquidity)
    zone_to_zone_target = float(sellside_liquidity) if magnet_side == "BUY" else float(buyside_liquidity)

    return {
        "magnet_side": magnet_side,
        "magnet_price": float(magnet_price),
        "zone_to_zone_target": float(zone_to_zone_target),
        "sellside_liquidity": float(sellside_liquidity),
        "buyside_liquidity": float(buyside_liquidity),
        "atr_h1": float(atr_h1) if atr_h1 is not None else None,
        "h1_close": float(last_h1.close),
        "h1_time_utc": _as_utc(last_h1.time_utc).isoformat(),
    }


def recompute_targets_snapshot(
    db: Session,
    *,
    symbol: str,
    tier: str = "pro",
    price_bid: float | None = None,
    price_ask: float | None = None,
    as_of_utc: datetime | None = None,
    reason: str = "scheduled",
    hit_context: dict[str, Any] | None = None,
    prefer_side: str | None = None,
) -> OracleTargetsSnapshot:
    symbol_value = symbol.strip().upper()
    tier_value = (tier or "pro").strip().lower()
    now_utc = _as_utc(as_of_utc or datetime.now(timezone.utc))

    latest_m1 = _latest_candle(db, symbol=symbol_value, timeframe="M1")
    fallback_price = float(latest_m1.close) if latest_m1 is not None else None
    bid = float(price_bid) if price_bid is not None else fallback_price
    ask = float(price_ask) if price_ask is not None else fallback_price
    if bid is None or ask is None:
        h1_fallback = _latest_candle(db, symbol=symbol_value, timeframe="H1")
        if not h1_fallback:
            raise ValueError(f"No market price available for {symbol_value}")
        bid = ask = float(h1_fallback.close)
    price_mid = (float(bid) + float(ask)) / 2.0

    levels = _compute_levels(db, symbol=symbol_value, price_mid=price_mid, prefer_side=prefer_side)
    latest = _latest_targets_row(db, symbol=symbol_value, tier=tier_value)
    should_send_liquidity_alert = (
        tier_value == "pro"
        and reason != "magnet_hit"
        and _liquidity_levels_changed_meaningfully(symbol=symbol_value, latest=latest, levels=levels)
    )

    previous: list[dict[str, Any]] = []
    if latest and isinstance(latest.magnet_state, dict):
        old_previous = latest.magnet_state.get("previous")
        if isinstance(old_previous, list):
            previous.extend([item for item in old_previous if isinstance(item, dict)])
        old_current = latest.magnet_state.get("current")
        if isinstance(old_current, dict):
            previous.append(old_current)
        previous = previous[-25:]

    magnet_state = {
        "current": {
            "price": levels["magnet_price"],
            "side": levels["magnet_side"],
            "computed_at_utc": now_utc.isoformat(),
            "reason": reason,
            "atr_h1": levels["atr_h1"],
            "h1_close": levels["h1_close"],
            "h1_time_utc": levels["h1_time_utc"],
        },
        "previous": previous,
        "hit": hit_context or None,
    }

    row = OracleTargetsSnapshot(
        symbol=symbol_value,
        tier=tier_value,
        timeframe_base="H1",
        as_of_utc=now_utc,
        price_bid=float(bid),
        price_ask=float(ask),
        magnet_price=float(levels["magnet_price"]),
        zone_to_zone_target=float(levels["zone_to_zone_target"]),
        sellside_liquidity=float(levels["sellside_liquidity"]),
        buyside_liquidity=float(levels["buyside_liquidity"]),
        magnet_state=magnet_state,
    )
    db.add(row)
    db.flush()
    _upsert_magnet_state(
        db,
        symbol=symbol_value,
        timeframe_base="H1",
        as_of_utc=now_utc,
        magnet_price=float(levels["magnet_price"]),
        magnet_side=str(levels["magnet_side"]).upper(),
        zone_to_zone_target=float(levels["zone_to_zone_target"]),
        sellside_liquidity=float(levels["sellside_liquidity"]),
        buyside_liquidity=float(levels["buyside_liquidity"]),
        state_json=magnet_state,
    )
    if should_send_liquidity_alert:
        try:
            alert_context = latest_oracle_alert_context(db, symbol=symbol_value)
            maybe_send_liquidity_target_alert(
                symbol=symbol_value,
                as_of_utc=now_utc,
                reason=str(reason or "").replace("_", " "),
                magnet=_safe_float(row.magnet_price),
                zone_target=_safe_float(row.zone_to_zone_target),
                sellside=_safe_float(row.sellside_liquidity),
                buyside=_safe_float(row.buyside_liquidity),
                daily_permission=alert_context.get("daily_permission"),
                permission_source=alert_context.get("permission_source"),
                permission_stage=alert_context.get("permission_stage"),
                final_allowed=alert_context.get("final_allowed"),
                h1_confirmation=alert_context.get("h1_confirmation"),
                m15_opportunity=alert_context.get("m15_opportunity"),
                confidence=_safe_float(alert_context.get("confidence")),
                risk_state=alert_context.get("risk_state"),
            )
        except Exception:
            logger.exception("liquidity target telegram notify failed symbol=%s reason=%s", symbol_value, reason)
    logger.info(
        "targets computed symbol=%s timeframe=%s tier=%s latest_candle_time=%s computed_at=%s snapshot_id=%s magnet=%s side=%s reason=%s",
        symbol_value,
        "H1",
        tier_value,
        levels["h1_time_utc"],
        now_utc.isoformat(),
        str(row.id),
        row.magnet_price,
        levels["magnet_side"],
        reason,
    )
    return row


def maybe_refresh_targets_on_magnet_hit(
    db: Session,
    *,
    symbol: str,
    bid: float,
    ask: float,
    m1_close: float | None = None,
    event_time_utc: datetime | None = None,
    tier: str = "pro",
) -> dict[str, Any]:
    symbol_value = symbol.strip().upper()
    tier_value = (tier or "pro").strip().lower()
    now_utc = _as_utc(event_time_utc or datetime.now(timezone.utc))

    latest = _latest_targets_row(db, symbol=symbol_value, tier=tier_value)
    if not latest:
        boot = recompute_targets_snapshot(
            db,
            symbol=symbol_value,
            tier=tier_value,
            price_bid=bid,
            price_ask=ask,
            as_of_utc=now_utc,
            reason="bootstrap",
        )
        return {"hit": False, "created": True, "snapshot_id": str(boot.id)}

    state = latest.magnet_state if isinstance(latest.magnet_state, dict) else {}
    current = state.get("current") if isinstance(state.get("current"), dict) else {}
    current_side = str(current.get("side") or "BUY").upper()
    current_price = _safe_float(current.get("price"))
    atr_h1 = _safe_float(current.get("atr_h1"))
    if current_price is None:
        return {"hit": False, "reason": "missing_current_magnet"}

    hit_result = detect_magnet_hit(
        magnet_side=current_side,
        magnet_price=float(current_price),
        bid=float(bid),
        ask=float(ask),
        atr_h1=atr_h1,
        m1_close=m1_close,
    )
    if not bool(hit_result.get("hit")):
        return {"hit": False, "reason": hit_result.get("reason"), "tolerance": hit_result.get("tolerance")}

    hit_side = str(hit_result.get("side") or current_side).upper()
    next_side = "SELL" if hit_side == "BUY" else "BUY"
    hit_context = {
        "hit_at_utc": now_utc.isoformat(),
        "hit_price": hit_result.get("hit_price"),
        "hit_side": hit_side,
        "confidence": hit_result.get("confidence"),
        "tolerance": hit_result.get("tolerance"),
        "reason": hit_result.get("reason"),
    }
    logger.info(
        "magnet hit symbol=%s tier=%s side=%s magnet=%s hit_price=%s",
        symbol_value,
        tier_value,
        hit_side,
        current_price,
        hit_context["hit_price"],
    )
    new_row = recompute_targets_snapshot(
        db,
        symbol=symbol_value,
        tier=tier_value,
        price_bid=float(bid),
        price_ask=float(ask),
        as_of_utc=now_utc,
        reason="magnet_hit",
        hit_context=hit_context,
        prefer_side=next_side,
    )
    if tier_value == "pro":
        try:
            alert_context = latest_oracle_alert_context(db, symbol=symbol_value)
            maybe_send_liquidity_target_alert(
                symbol=symbol_value,
                as_of_utc=now_utc,
                reason=f"{hit_result.get('reason') or 'magnet_hit'} after {hit_side} magnet hit",
                magnet=_safe_float(new_row.magnet_price),
                zone_target=_safe_float(new_row.zone_to_zone_target),
                sellside=_safe_float(new_row.sellside_liquidity),
                buyside=_safe_float(new_row.buyside_liquidity),
                daily_permission=alert_context.get("daily_permission"),
                permission_source=alert_context.get("permission_source"),
                permission_stage=alert_context.get("permission_stage"),
                final_allowed=alert_context.get("final_allowed"),
                h1_confirmation=alert_context.get("h1_confirmation"),
                m15_opportunity=alert_context.get("m15_opportunity"),
                confidence=_safe_float(alert_context.get("confidence")),
                risk_state=alert_context.get("risk_state"),
            )
        except Exception:
            logger.exception("pro magnet telegram notify failed symbol=%s", symbol_value)
    return {"hit": True, "snapshot_id": str(new_row.id), "new_magnet_price": new_row.magnet_price}


def refresh_targets_for_all_symbols(
    db: Session,
    *,
    symbols: list[str] | None = None,
    reason: str,
    tiers: list[str] | None = None,
    as_of_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    target_symbols = symbols or enabled_symbols_from_settings()
    target_tiers = tiers or ["pro", "elite"]
    results: list[dict[str, Any]] = []
    for symbol in target_symbols:
        for tier in target_tiers:
            try:
                row = recompute_targets_snapshot(
                    db,
                    symbol=symbol,
                    tier=tier,
                    as_of_utc=as_of_utc,
                    reason=reason,
                )
                results.append(
                    {
                        "ok": True,
                        "symbol": row.symbol,
                        "tier": row.tier,
                        "as_of_utc": _as_utc(row.as_of_utc).isoformat(),
                        "magnet_price": row.magnet_price,
                    }
                )
            except Exception as exc:
                logger.exception("targets recompute failed symbol=%s tier=%s reason=%s", symbol, tier, reason)
                results.append({"ok": False, "symbol": symbol, "tier": tier, "error": str(exc)})
    return results


def ingest_latest_m1_candles(
    db: Session,
    *,
    symbols: list[str] | None = None,
) -> list[dict[str, Any]]:
    return ingest_latest_candles(db, symbols=symbols, timeframes=["M1"])


def ingest_latest_candles(
    db: Session,
    *,
    symbols: list[str] | None = None,
    timeframes: list[str] | None = None,
) -> list[dict[str, Any]]:
    provider = get_data_provider()
    api_mode = api_candle_mode()
    target_symbols = symbols or enabled_symbols_from_settings()
    target_timeframes = [tf.strip().upper() for tf in (timeframes or ["M1"]) if tf and tf.strip()]
    if not target_timeframes:
        target_timeframes = ["M1"]
    results: list[dict[str, Any]] = []

    for symbol in target_symbols:
        symbol_value = symbol.strip().upper()
        latest_symbol_time: datetime | None = None
        for timeframe in target_timeframes:
            try:
                candle = provider.get_latest_closed_candle(symbol=symbol_value, timeframe=timeframe)
                candle_time = _as_utc(candle.time_utc)
                created = _upsert_candle(
                    db,
                    symbol=symbol_value,
                    timeframe=timeframe,
                    candle_time_utc=candle_time,
                    open_=float(candle.open),
                    high=float(candle.high),
                    low=float(candle.low),
                    close=float(candle.close),
                    volume=candle.volume,
                )
                latest_symbol_time = candle_time if latest_symbol_time is None else max(latest_symbol_time, candle_time)
                results.append(
                    {
                        "ok": True,
                        "symbol": symbol_value,
                        "timeframe": timeframe,
                        "created": created,
                        "time_open_utc": candle_time.isoformat(),
                    }
                )
                if api_mode:
                    logger.info(
                        "api_candle_ingest_write_ok symbol=%s timeframe=%s candle_time_utc=%s source=%s created=%s",
                        symbol_value,
                        timeframe,
                        candle_time.isoformat(),
                        getattr(candle, "source", None) or getattr(provider, "name", "api"),
                        created,
                    )
            except Exception as exc:
                logger.exception("market ingest failed symbol=%s timeframe=%s", symbol_value, timeframe)
                if api_mode:
                    logger.warning(
                        "api_candle_ingest_write_failed symbol=%s timeframe=%s error=%s",
                        symbol_value,
                        timeframe,
                        exc,
                    )
                results.append({"ok": False, "symbol": symbol_value, "timeframe": timeframe, "error": str(exc)})

        if latest_symbol_time is not None:
            status_row = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol == symbol_value).first()
            broker_offset_seconds: int | None = None
            if status_row is not None and status_row.broker_offset_seconds is not None:
                broker_offset_seconds = int(status_row.broker_offset_seconds)
                BROKER_OFFSET_CACHE_SECONDS[symbol_value] = broker_offset_seconds
            if not status_row:
                status_row = MT5IngestStatus(
                    symbol=symbol_value,
                    last_ingested_at=latest_symbol_time,
                    broker_offset_seconds=broker_offset_seconds if broker_offset_seconds is not None else 0,
                    broker_offset_detected_at=None,
                )
                db.add(status_row)
            else:
                if _as_utc(status_row.last_ingested_at) < latest_symbol_time:
                    status_row.last_ingested_at = latest_symbol_time
                db.add(status_row)

    successful_symbols = sorted({str(item.get("symbol") or "") for item in results if item.get("ok") and item.get("symbol")})
    logger.info(
        "market ingest run completed requested_symbols=%s successful_symbols=%s timeframes=%s",
        ",".join(target_symbols),
        ",".join(successful_symbols),
        ",".join(target_timeframes),
    )
    return results


def market_health_rows(db: Session, *, symbols: list[str] | None = None) -> list[dict[str, Any]]:
    now_utc = datetime.now(timezone.utc)
    target_symbols = symbols or enabled_symbols_from_settings()
    rows = db.query(MT5IngestStatus).filter(MT5IngestStatus.symbol.in_(target_symbols)).all()
    by_symbol = {row.symbol: row for row in rows}
    payload: list[dict[str, Any]] = []
    for symbol in target_symbols:
        row = by_symbol.get(symbol)
        if not row:
            offset = get_cached_broker_offset_seconds(symbol)
            payload.append(
                {
                    "symbol": symbol,
                    "last_ingest_time_utc": None,
                    "lag_seconds": None,
                    "broker_offset_seconds": offset,
                    "broker_offset_hours": (round(float(offset) / 3600.0, 3) if offset is not None else None),
                }
            )
            continue
        last = _as_utc(row.last_ingested_at)
        offset = (
            int(row.broker_offset_seconds)
            if row.broker_offset_seconds is not None
            else get_cached_broker_offset_seconds(symbol)
        )
        payload.append(
            {
                "symbol": symbol,
                "last_ingest_time_utc": last.isoformat(),
                "lag_seconds": max(int((now_utc - last).total_seconds()), 0),
                "broker_offset_seconds": offset,
                "broker_offset_hours": round(float(offset) / 3600.0, 3) if offset is not None else None,
            }
        )
    return payload
