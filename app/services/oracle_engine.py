from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import MT5Candle
from app.services.h4_session_modifier import apply_h4_session_flip_modifier
from app.services.manipulation_m15 import detect_manipulation_m15
from app.services.time_service import TimeService

logger = logging.getLogger(__name__)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _uk_tz() -> tuple[timezone | ZoneInfo, bool]:
    try:
        return ZoneInfo("Europe/London"), True
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401

            return ZoneInfo("Europe/London"), True
        except Exception:
            return timezone.utc, False


UK_TZ, UK_TZ_AVAILABLE = _uk_tz()


def _to_uk_date(value: datetime) -> date:
    return _as_utc(value).astimezone(UK_TZ).date()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema_value = values[0]
    for v in values[1:]:
        ema_value = (v * k) + (ema_value * (1 - k))
    return ema_value


def _atr(candles: list[MT5Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(
            float(c.high) - float(c.low),
            abs(float(c.high) - float(p.close)),
            abs(float(c.low) - float(p.close)),
        )
        trs.append(tr)
    window = trs[-period:]
    return sum(window) / len(window) if window else 0.0


def _latest_closed_candles(db: Session, symbol: str, timeframe: str, limit: int) -> list[MT5Candle]:
    rows = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(limit)
        .all()
    )
    rows.reverse()
    return rows


@dataclass
class CandidateResult:
    symbol: str
    timeframe: str
    as_of_utc: datetime
    bias: str
    confidence: float
    internal_json: dict
    public_json: dict


@dataclass
class ConfirmResult:
    confirm_ok: bool
    as_of_utc: datetime
    reason_json: dict
    manipulation_score: int
    manipulation_level: str
    manipulation_reasons: list[str]
    m15_volume_state: str


@dataclass
class QuarterlySnapshotResult:
    symbol: str
    quarter_key: str
    quarter_open: float
    q_high_to_date: float
    q_low_to_date: float
    q_mid_to_date: float
    premium_discount: str
    quarterly_bias: str
    permission_mode: str
    conflict_rule: str
    confidence: float
    factors: dict
    as_of_utc: datetime


@dataclass
class PermissionDecisionResult:
    symbol: str
    date_uk: date
    allowed_direction_final: str
    daily_bias_raw: str
    quarterly_bias: str
    alignment: str
    confidence_final: float
    message_tag: str
    as_of_utc: datetime
    allowed_direction_final_strict: str
    allowed_direction_final_soft: str
    details: dict


@dataclass
class WeeklyRangeSnapshotResult:
    symbol: str
    week_key: str
    week_start_uk: date
    high: float
    low: float
    mid: float
    range_ready: bool
    as_of_utc: datetime
    meta_json: dict


@dataclass
class DailyPermissionResult:
    symbol: str
    date_uk: date
    timeframe: str
    as_of_utc: datetime
    daily_permission: str
    reason: str
    spread: float | None
    volatility: float | None
    is_extreme: bool
    factors_json: dict
    daily_permission_stage: str = "OFFICIAL"
    permission_source: str = "LONDON_0801"
    official: bool = True
    computed_at_utc: datetime | None = None
    for_date: date | None = None
    confidence: float | None = None
    reasons: list[str] | None = None


@dataclass
class OpportunityResult:
    symbol: str
    as_of_utc: datetime
    timeframe_signal: str
    timeframe_confirm: str
    opportunity_direction: str
    daily_permission: str
    aligned: bool
    h1_confirm_ok: bool
    final_allowed: str
    confidence: float
    reason: str
    public_json: dict
    internal_json: dict


def _quarter_key_and_start(value: date) -> tuple[str, date]:
    quarter = ((value.month - 1) // 3) + 1
    start_month = ((quarter - 1) * 3) + 1
    key = f"{value.year}-Q{quarter}"
    start = date(value.year, start_month, 1)
    return key, start


def _week_key_and_start(value: date) -> tuple[str, date]:
    week_start = value - timedelta(days=value.weekday())
    iso = week_start.isocalendar()
    week_key = f"{iso.year}-W{iso.week:02d}"
    return week_key, week_start


def _tier_warning_copy(*, blueprint_day: bool, volume_spike: bool) -> dict[str, str]:
    if blueprint_day and volume_spike:
        return {
            "basic": "Blueprint day and abnormal volume are active. Use defensive risk only.",
            "pro": "Blueprint day plus volume spike. Prioritize confirmation and reduce update frequency.",
            "elite": "Blueprint and volume-spike regime. Tighten execution and wait for clean continuation.",
        }
    if blueprint_day:
        return {
            "basic": "Blueprint day conditions. Reduce size and avoid forcing entries.",
            "pro": "Blueprint day conditions. Treat early moves as setup-building, not chase triggers.",
            "elite": "Blueprint day conditions. Keep execution conservative until structure stabilizes.",
        }
    if volume_spike:
        return {
            "basic": "Abnormal volume detected. Stand down unless conditions normalize.",
            "pro": "Volume spike detected. Fade risk and require cleaner follow-through.",
            "elite": "Volume-spike regime. Keep position sizing defensive and confirmations strict.",
        }
    return {
        "basic": "Risk conditions normal.",
        "pro": "Risk conditions normal.",
        "elite": "Risk conditions normal.",
    }


def _compute_volume_spike_state(db: Session, *, symbol: str, as_of_utc: datetime) -> dict:
    rows = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M15",
            MT5Candle.time_utc <= _as_utc(as_of_utc),
        )
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(30)
        .all()
    )
    rows.reverse()
    if not rows:
        return {
            "volume_spike": False,
            "volume_ratio": 0.0,
            "last_m15_volume": None,
            "median_m15_volume_20": None,
            "volume_as_of_utc": None,
        }

    last = rows[-1]
    last_volume = float(last.volume) if last.volume is not None else 0.0
    lookback = [float(c.volume or 0.0) for c in rows[-20:]]
    med = float(median(lookback)) if lookback else 0.0
    ratio = (last_volume / med) if med > 0 else 0.0
    spike = bool(med > 0 and ratio >= 2.0)

    return {
        "volume_spike": spike,
        "volume_ratio": round(ratio, 4),
        "last_m15_volume": round(last_volume, 4),
        "median_m15_volume_20": round(med, 4),
        "volume_as_of_utc": _as_utc(last.time_utc).isoformat(),
    }


def _latest_price_candle(db: Session, symbol: str) -> MT5Candle | None:
    row = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == "H1")
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )
    if row:
        return row
    return (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == "D1")
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )


def compute_quarterly_snapshot(
    db: Session,
    *,
    symbol: str,
    as_of_utc: datetime | None = None,
) -> QuarterlySnapshotResult:
    ref_utc = _as_utc(as_of_utc or datetime.now(timezone.utc))
    ref_uk_date = _to_uk_date(ref_utc)
    quarter_key, quarter_start_uk = _quarter_key_and_start(ref_uk_date)
    quarter_start_utc = datetime(
        quarter_start_uk.year,
        quarter_start_uk.month,
        quarter_start_uk.day,
        tzinfo=UK_TZ,
    ).astimezone(timezone.utc)

    d1 = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "D1",
            MT5Candle.time_utc >= quarter_start_utc,
            MT5Candle.time_utc <= ref_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )
    last_price_candle = _latest_price_candle(db, symbol)
    if not last_price_candle and not d1:
        raise ValueError("No D1/H1 candles available for quarterly snapshot")

    if d1:
        quarter_open = float(d1[0].open)
        q_high = max(float(c.high) for c in d1)
        q_low = min(float(c.low) for c in d1)
        as_of_value = _as_utc(d1[-1].time_utc)
    else:
        fallback_price = float(last_price_candle.close)
        quarter_open = fallback_price
        q_high = fallback_price
        q_low = fallback_price
        as_of_value = _as_utc(last_price_candle.time_utc)

    if last_price_candle:
        last_price = float(last_price_candle.close)
        as_of_value = _as_utc(last_price_candle.time_utc)
    else:
        last_price = float(d1[-1].close)

    q_range = max(q_high - q_low, 1e-9)
    q_mid = (q_high + q_low) / 2.0
    near_open_threshold = q_range * 0.15

    if abs(last_price - quarter_open) <= near_open_threshold:
        premium_discount = "near_open"
    elif last_price >= q_mid:
        premium_discount = "premium"
    else:
        premium_discount = "discount"

    insufficient_data = len(d1) < 3
    min_viable_range = max(abs(quarter_open) * 0.0015, 0.0005)
    small_range = q_range <= min_viable_range

    if insufficient_data or small_range or premium_discount == "near_open":
        quarterly_bias = "BOTH"
    elif premium_discount == "premium":
        quarterly_bias = "SELL_ONLY"
    else:
        quarterly_bias = "BUY_ONLY"

    h1 = _latest_closed_candles(db, symbol=symbol, timeframe="H1", limit=2)
    trend_h1 = "neutral"
    trend_adjust = 0.0
    if h1:
        last_h1 = h1[-1]
        if float(last_h1.close) > float(last_h1.open):
            trend_h1 = "bullish"
        elif float(last_h1.close) < float(last_h1.open):
            trend_h1 = "bearish"
        if quarterly_bias == "BUY_ONLY" and trend_h1 == "bullish":
            trend_adjust = 0.10
        elif quarterly_bias == "SELL_ONLY" and trend_h1 == "bearish":
            trend_adjust = 0.10
        elif quarterly_bias in {"BUY_ONLY", "SELL_ONLY"} and trend_h1 != "neutral":
            trend_adjust = -0.10

    base_conf = 0.62 if quarterly_bias in {"BUY_ONLY", "SELL_ONLY"} else 0.42
    if premium_discount == "near_open":
        base_conf -= 0.08
    if insufficient_data or small_range:
        base_conf = min(base_conf, 0.35)
    confidence = round(_clamp(base_conf + trend_adjust, 0.05, 0.95), 4)

    permission_mode = "STRICT" if quarterly_bias in {"BUY_ONLY", "SELL_ONLY"} else "SOFT"
    conflict_rule = "BLOCK_COUNTER" if permission_mode == "STRICT" else "DOWNGRADE_COUNTER"
    factors = {
        "last_price": last_price,
        "trend_h1": trend_h1,
        "q_range": q_range,
        "near_open_threshold": near_open_threshold,
        "insufficient_data": insufficient_data,
        "small_range": small_range,
        "min_viable_range": min_viable_range,
    }

    return QuarterlySnapshotResult(
        symbol=symbol,
        quarter_key=quarter_key,
        quarter_open=round(quarter_open, 6),
        q_high_to_date=round(q_high, 6),
        q_low_to_date=round(q_low, 6),
        q_mid_to_date=round(q_mid, 6),
        premium_discount=premium_discount,
        quarterly_bias=quarterly_bias,
        permission_mode=permission_mode,
        conflict_rule=conflict_rule,
        confidence=confidence,
        factors=factors,
        as_of_utc=as_of_value,
    )


def compute_permission_decision(
    db: Session,
    *,
    symbol: str,
    daily_bias_raw: str,
    daily_confidence: float | None = None,
    as_of_utc: datetime | None = None,
    quarterly_snapshot: QuarterlySnapshotResult | None = None,
) -> PermissionDecisionResult:
    quarter = quarterly_snapshot or compute_quarterly_snapshot(db, symbol=symbol, as_of_utc=as_of_utc)
    bias = daily_bias_raw if daily_bias_raw in {"BUY_ONLY", "SELL_ONLY", "NO_TRADE"} else "NO_TRADE"
    q_bias = quarter.quarterly_bias
    as_of = _as_utc(as_of_utc or quarter.as_of_utc)

    if q_bias in {"BOTH", "NO_TRADE"} or bias == "NO_TRADE":
        alignment = "NEUTRAL"
    elif bias == q_bias:
        alignment = "ALIGNED"
    else:
        alignment = "CONFLICT"

    allowed_strict = bias
    allowed_soft = bias
    if bias == "NO_TRADE":
        allowed_strict = "NO_TRADE"
        allowed_soft = "NO_TRADE"
    elif q_bias == "NO_TRADE":
        allowed_strict = "NO_TRADE"
        allowed_soft = "NO_TRADE"
    elif alignment == "CONFLICT":
        allowed_strict = "NO_TRADE"
        allowed_soft = bias
    elif q_bias == "BOTH":
        allowed_strict = bias
        allowed_soft = bias

    if alignment == "ALIGNED":
        message_tag = "TREND_DAY_OK"
    elif alignment == "CONFLICT":
        message_tag = "COUNTERTREND_CAUTION"
    else:
        message_tag = "NO_TRADE_FILTER" if allowed_strict == "NO_TRADE" else "TREND_DAY_OK"

    daily_conf = _clamp(float(daily_confidence if daily_confidence is not None else 0.5), 0.0, 1.0)
    combined = (daily_conf * 0.55) + (quarter.confidence * 0.45)
    if alignment == "ALIGNED":
        combined += 0.10
    elif alignment == "CONFLICT":
        combined -= 0.20
    elif q_bias == "BOTH":
        combined -= 0.08
    if allowed_strict == "NO_TRADE" and bias != "NO_TRADE":
        combined = min(combined, 0.45)
    confidence_final = round(_clamp(combined, 0.05, 0.99), 4)

    details = {
        "quarter_key": quarter.quarter_key,
        "premium_discount": quarter.premium_discount,
        "permission_mode": quarter.permission_mode,
        "conflict_rule": quarter.conflict_rule,
        "allowed_direction_final_strict": allowed_strict,
        "allowed_direction_final_soft": allowed_soft,
        "quarterly_confidence": quarter.confidence,
        "daily_confidence": daily_conf,
        "factors": quarter.factors,
    }

    return PermissionDecisionResult(
        symbol=symbol,
        date_uk=_to_uk_date(as_of),
        allowed_direction_final=allowed_strict,
        daily_bias_raw=bias,
        quarterly_bias=q_bias,
        alignment=alignment,
        confidence_final=confidence_final,
        message_tag=message_tag,
        as_of_utc=as_of,
        allowed_direction_final_strict=allowed_strict,
        allowed_direction_final_soft=allowed_soft,
        details=details,
    )


def compute_weekly_range_snapshot(
    db: Session,
    *,
    symbol: str,
    as_of_utc: datetime | None = None,
) -> WeeklyRangeSnapshotResult:
    ref_utc = _as_utc(as_of_utc or datetime.now(timezone.utc))
    ref_uk = ref_utc.astimezone(UK_TZ)
    week_key, week_start_date_uk = _week_key_and_start(ref_uk.date())
    week_start_local = datetime(
        week_start_date_uk.year,
        week_start_date_uk.month,
        week_start_date_uk.day,
        0,
        0,
        0,
        tzinfo=UK_TZ,
    )
    week_start_utc = week_start_local.astimezone(timezone.utc)
    lock_time_local = week_start_local + timedelta(days=1, hours=12)  # Tuesday 12:00 UK

    h1_rows = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "H1",
            MT5Candle.time_utc >= week_start_utc,
            MT5Candle.time_utc <= ref_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )

    if h1_rows:
        high = max(float(c.high) for c in h1_rows)
        low = min(float(c.low) for c in h1_rows)
        as_of_value = _as_utc(h1_rows[-1].time_utc)
    else:
        fallback = (
            db.query(MT5Candle)
            .filter(
                MT5Candle.symbol == symbol,
                MT5Candle.time_utc <= ref_utc,
            )
            .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
            .first()
        )
        if not fallback:
            raise ValueError("No candles available to compute weekly range")
        price = float(fallback.close)
        high = price
        low = price
        as_of_value = _as_utc(fallback.time_utc)

    mid = (high + low) / 2.0
    locked_by_time = ref_uk >= lock_time_local
    locked_by_candle_count = len(h1_rows) >= 24
    range_ready = bool(locked_by_time or locked_by_candle_count)

    meta_json = {
        "h1_candle_count": len(h1_rows),
        "lock_time_uk": lock_time_local.isoformat(),
        "locked_by_time": locked_by_time,
        "locked_by_candle_count": locked_by_candle_count,
        "status": "Locked" if range_ready else "Building",
    }
    return WeeklyRangeSnapshotResult(
        symbol=symbol,
        week_key=week_key,
        week_start_uk=week_start_date_uk,
        high=round(high, 6),
        low=round(low, 6),
        mid=round(mid, 6),
        range_ready=range_ready,
        as_of_utc=as_of_value,
        meta_json=meta_json,
    )


def direction_for_tier(permission: PermissionDecisionResult, plan: str) -> str:
    tier = (plan or "basic").lower()
    if tier == "basic":
        return permission.allowed_direction_final_strict
    return permission.allowed_direction_final_soft


def compute_hourly_candidate(db: Session, symbol: str = "XAUUSD") -> CandidateResult:
    timeframe_used = "H1"
    candles: list[MT5Candle] = []
    for timeframe in ("H1", "M15", "M1"):
        candidate = _latest_closed_candles(db, symbol=symbol, timeframe=timeframe, limit=24)
        if len(candidate) >= 1:
            candles = candidate
            timeframe_used = timeframe
            break
    if len(candles) < 1:
        raise ValueError("No closed candles available for H1/M15/M1")

    last = candles[-1]
    prev = candles[-2] if len(candles) >= 2 else None

    open_ = float(last.open)
    high = float(last.high)
    low = float(last.low)
    close = float(last.close)
    range_ = max(high - low, 1e-9)
    body_ratio = min(max(abs(close - open_) / range_, 0.0), 1.0)

    if close > open_:
        bias = "BUY_ONLY"
    elif close < open_:
        bias = "SELL_ONLY"
    else:
        bias = "NO_TRADE"

    prev_close = float(prev.close) if prev else close
    trend_bonus = 0.0
    if prev and bias == "BUY_ONLY" and close > prev_close:
        trend_bonus = 0.08
    if prev and bias == "SELL_ONLY" and close < prev_close:
        trend_bonus = 0.08

    confidence = round(min(max((0.35 + (body_ratio * 0.55) + trend_bonus), 0.0), 0.99), 4)
    if bias == "NO_TRADE":
        confidence = round(min(confidence, 0.4), 4)

    h4_candles = _latest_closed_candles(db, symbol=symbol, timeframe="H4", limit=2)
    h4_last = h4_candles[-1] if h4_candles else None
    h4_prev = h4_candles[-2] if len(h4_candles) >= 2 else None
    h4_modifier = apply_h4_session_flip_modifier(
        symbol=symbol,
        allowed_direction=bias,
        confidence=confidence,
        last_h4_candle=h4_last,
        prev_h4_candle=h4_prev,
        liquidity_last_sweep=None,
        pdh_pdl=None,
        h4_atr=None,
    )
    confidence = round(h4_modifier.modified_confidence, 4)

    range_window = candles[-10:]
    avg_range = sum(float(c.high) - float(c.low) for c in range_window) / max(len(range_window), 1)
    volatility_ratio = (range_ / avg_range) if avg_range > 0 else 1.0
    if volatility_ratio >= 1.5:
        vol_state = "high"
    elif volatility_ratio <= 0.75:
        vol_state = "low"
    else:
        vol_state = "normal"

    atr_h1 = _atr(candles[-20:], period=14)
    session_label = "Hourly Bias"
    liquidity_high = round(max(float(c.high) for c in candles[-6:]), 2)
    liquidity_low = round(min(float(c.low) for c in candles[-6:]), 2)

    if bias == "BUY_ONLY":
        target = round(close + (range_ * 1.2), 2)
        reaction = round(close - (range_ * 0.6), 2)
        c1 = "H1 close finished above open with positive body expansion."
        c2 = "Recent closes are holding above prior hourly median."
        l1 = f"Upper liquidity pool near {liquidity_high:.2f}."
        l2 = f"Lower sweep reference near {liquidity_low:.2f}."
        m1 = "Directional pressure is favoring continuation higher."
        m2 = "Order flow remains constructive on the latest close."
        p1 = "Prefer pullback participation into H1 structure."
        p2 = "Invalidate quickly if momentum stalls below reaction zone."
    elif bias == "SELL_ONLY":
        target = round(close - (range_ * 1.2), 2)
        reaction = round(close + (range_ * 0.6), 2)
        c1 = "H1 close finished below open with negative body expansion."
        c2 = "Recent closes are holding below prior hourly median."
        l1 = f"Lower liquidity pool near {liquidity_low:.2f}."
        l2 = f"Upper sweep reference near {liquidity_high:.2f}."
        m1 = "Directional pressure is favoring continuation lower."
        m2 = "Order flow remains defensive on the latest close."
        p1 = "Prefer pullback participation into H1 structure."
        p2 = "Invalidate quickly if momentum stalls above reaction zone."
    else:
        target = round(close, 2)
        reaction = round(close, 2)
        c1 = "H1 body is indecisive."
        c2 = "Directional edge is weak from current candle structure."
        l1 = f"Upper liquidity pool near {liquidity_high:.2f}."
        l2 = f"Lower liquidity pool near {liquidity_low:.2f}."
        m1 = "State is rotational."
        m2 = "Standby until structure resolves."
        p1 = "Do not force direction while range persists."
        p2 = "Wait for clearer displacement."

    if h4_modifier.reasons_public:
        c2 = f"{c2} {' '.join(h4_modifier.reasons_public)}".strip()

    as_of = _as_utc(last.time_utc)
    weekly_snapshot = compute_weekly_range_snapshot(db, symbol=symbol, as_of_utc=as_of)
    volume_spike_state = _compute_volume_spike_state(db, symbol=symbol, as_of_utc=as_of)
    is_blueprint_day = _to_uk_date(as_of).weekday() == 0
    volume_spike = bool(volume_spike_state.get("volume_spike"))
    risk_reasons: list[str] = []
    suggested_risk_multiplier = 1.0
    if is_blueprint_day:
        suggested_risk_multiplier = min(suggested_risk_multiplier, 0.5)
        risk_reasons.append("Blueprint Day: early-week structure is still forming.")
    if volume_spike:
        suggested_risk_multiplier = min(suggested_risk_multiplier, 0.25)
        ratio = float(volume_spike_state.get("volume_ratio") or 0.0)
        risk_reasons.append(f"M15 volume spike detected ({ratio:.2f}x rolling median).")
    tier_copy = _tier_warning_copy(blueprint_day=is_blueprint_day, volume_spike=volume_spike)
    risk_banner = {
        "is_blueprint_day": is_blueprint_day,
        "volume_spike": volume_spike,
        "suggested_risk_multiplier": round(suggested_risk_multiplier, 2),
        "reasons": risk_reasons,
        "tier_copy": tier_copy,
        "volume_ratio": volume_spike_state.get("volume_ratio"),
        "last_m15_volume": volume_spike_state.get("last_m15_volume"),
        "median_m15_volume_20": volume_spike_state.get("median_m15_volume_20"),
    }
    weekly_range = {
        "symbol": weekly_snapshot.symbol,
        "week_key": weekly_snapshot.week_key,
        "week_start_uk": weekly_snapshot.week_start_uk.isoformat(),
        "high": weekly_snapshot.high,
        "low": weekly_snapshot.low,
        "mid": weekly_snapshot.mid,
        "range_ready": weekly_snapshot.range_ready,
        "status": "Locked" if weekly_snapshot.range_ready else "Building",
        "as_of_utc": weekly_snapshot.as_of_utc.isoformat(),
        "meta_json": weekly_snapshot.meta_json,
    }
    public_json = {
        "symbol": symbol,
        "as_of_utc": as_of.isoformat(),
        "session_label": session_label,
        "bias": bias,
        "confidence": confidence,
        "c1": c1,
        "c2": c2,
        "l1": l1,
        "l2": l2,
        "target": f"{target:.2f}",
        "reaction": f"{reaction:.2f}",
        "m1": m1,
        "m2": m2,
        "p1": p1,
        "p2": p2,
        "vol_state": vol_state,
        "atr_h1": round(atr_h1, 4),
        "liquidity_map": {"l1": l1, "l2": l2},
        "targets_json": {
            "target": target,
            "reaction": reaction,
            "liquidity_high": liquidity_high,
            "liquidity_low": liquidity_low,
        },
        "h4_session_modifier": {
            "applied": h4_modifier.applied,
            "key_window": h4_modifier.key_window,
            "confidence_delta": h4_modifier.confidence_delta,
            "modified_confidence": h4_modifier.modified_confidence,
            "reasons_public": h4_modifier.reasons_public,
        },
        "reasons_public": h4_modifier.reasons_public,
        "risk_banner": risk_banner,
        "weekly_range": weekly_range,
    }
    internal_json = {
        "timeframe": timeframe_used,
        "body_ratio": body_ratio,
        "volatility_ratio": volatility_ratio,
        "atr_h1": atr_h1,
        "candle": {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": float(last.volume) if last.volume is not None else None,
        },
        "prev_close": prev_close,
        "volume_spike_state": volume_spike_state,
        "weekly_range_snapshot": {
            "week_key": weekly_snapshot.week_key,
            "week_start_uk": weekly_snapshot.week_start_uk.isoformat(),
            "high": weekly_snapshot.high,
            "low": weekly_snapshot.low,
            "mid": weekly_snapshot.mid,
            "range_ready": weekly_snapshot.range_ready,
            "as_of_utc": weekly_snapshot.as_of_utc.isoformat(),
            "meta_json": weekly_snapshot.meta_json,
        },
        "risk_banner": risk_banner,
        "h4_session_modifier": {
            "applied": h4_modifier.applied,
            "key_window": h4_modifier.key_window,
            "confidence_delta": h4_modifier.confidence_delta,
            "modified_confidence": h4_modifier.modified_confidence,
            "reasons_internal": h4_modifier.reasons_internal,
        },
    }

    return CandidateResult(
        symbol=symbol,
        timeframe=timeframe_used,
        as_of_utc=as_of,
        bias=bias,
        confidence=confidence,
        internal_json=internal_json,
        public_json=public_json,
    )


def confirm_with_m15(
    db: Session,
    *,
    symbol: str,
    candidate_bias: str,
    candidate_as_of_utc: datetime | None = None,
) -> ConfirmResult:
    if candidate_bias == "NO_TRADE":
        return ConfirmResult(
            confirm_ok=False,
            as_of_utc=datetime.now(timezone.utc),
            reason_json={"reason": "candidate_no_trade", "confirm_tf": "M15"},
            manipulation_score=0,
            manipulation_level="low",
            manipulation_reasons=["Candidate bias is NO_TRADE."],
            m15_volume_state="normal",
        )

    candles = _latest_closed_candles(db, symbol=symbol, timeframe="M15", limit=160)
    if len(candles) < 25:
        return ConfirmResult(
            confirm_ok=False,
            as_of_utc=datetime.now(timezone.utc),
            reason_json={"reason": "m15_history_missing", "confirm_tf": "M15"},
            manipulation_score=0,
            manipulation_level="low",
            manipulation_reasons=["Insufficient M15 candles for confirmation."],
            m15_volume_state="normal",
        )

    if candidate_as_of_utc:
        as_of = _as_utc(candidate_as_of_utc)
        post = [c for c in candles if _as_utc(c.time_utc) > as_of]
        if len(post) >= 2:
            eval_candles = post[-3:]
        else:
            eval_candles = candles[-3:]
    else:
        eval_candles = candles[-3:]

    closes = [float(c.close) for c in candles[-60:]]
    ema20 = _ema(closes, period=20)

    last = eval_candles[-1]
    last_open = float(last.open)
    last_close = float(last.close)
    last_prev_close = float(eval_candles[-2].close) if len(eval_candles) > 1 else last_close

    confirm_buy = (last_close > ema20) and (last_close > last_open) and (last_close >= last_prev_close)
    confirm_sell = (last_close < ema20) and (last_close < last_open) and (last_close <= last_prev_close)

    if candidate_bias == "BUY_ONLY":
        confirm_ok = confirm_buy
    else:
        confirm_ok = confirm_sell

    manip = detect_manipulation_m15(
        candles,
        lookback=settings.ORACLE_M15_MANIPULATION_LOOKBACK,
        z_window=settings.ORACLE_M15_MANIPULATION_Z_WINDOW,
    )

    if manip.volume_z >= 2:
        volume_state = "high"
    elif manip.volume_z <= -1:
        volume_state = "low"
    else:
        volume_state = "normal"

    as_of_utc = _as_utc(last.time_utc)
    reason_json = {
        "confirm_tf": "M15",
        "candidate_bias": candidate_bias,
        "confirm_ok": confirm_ok,
        "ema20": round(ema20, 4),
        "last_open": last_open,
        "last_close": last_close,
        "last_prev_close": last_prev_close,
        "manipulation_score": manip.score,
        "manipulation_level": manip.level,
        "manipulation_reasons": manip.reasons,
        "volume_z": manip.volume_z,
        "volume_state": volume_state,
    }
    return ConfirmResult(
        confirm_ok=confirm_ok,
        as_of_utc=as_of_utc,
        reason_json=reason_json,
        manipulation_score=manip.score,
        manipulation_level=manip.level,
        manipulation_reasons=manip.reasons,
        m15_volume_state=volume_state,
    )


def _daily_permission_target_utc(*, ref_utc: datetime) -> tuple[date, datetime]:
    local_now = _as_utc(ref_utc).astimezone(UK_TZ)
    date_uk = local_now.date()
    # Daily permission remains active until the next 08:01 London computation.
    if (local_now.hour, local_now.minute) < (8, 1):
        date_uk = (local_now - timedelta(days=1)).date()
    target_local = datetime(
        date_uk.year,
        date_uk.month,
        date_uk.day,
        8,
        1,
        tzinfo=UK_TZ,
    )
    return date_uk, target_local.astimezone(timezone.utc)


def _candle_range(candle: MT5Candle) -> float:
    return max(float(candle.high) - float(candle.low), 0.0)


def compute_prelim_permission_from_asia(
    db: Session,
    *,
    symbol: str,
    ref_utc: datetime | None = None,
) -> DailyPermissionResult:
    now_utc = _as_utc(ref_utc or datetime.now(timezone.utc))
    if not UK_TZ_AVAILABLE:
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=_to_uk_date(now_utc),
            for_date=_to_uk_date(now_utc),
            timeframe="M15",
            as_of_utc=now_utc,
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="PRELIM",
            permission_source="ASIA",
            official=False,
            reason="Europe/London timezone unavailable; PRELIM permission disabled.",
            spread=None,
            volatility=None,
            is_extreme=True,
            confidence=0.0,
            reasons=["timezone_unavailable"],
            factors_json={
                "timezone": "UTC_FALLBACK",
                "timezone_unavailable": True,
                "disable_prelim_asia_logic": True,
            },
        )

    local_now = now_utc.astimezone(UK_TZ)
    for_date = local_now.date()
    asia_start_local = datetime(
        for_date.year,
        for_date.month,
        for_date.day,
        int(settings.ORACLE_ASIA_START_HOUR),
        0,
        tzinfo=UK_TZ,
    )
    asia_end_local = datetime(
        for_date.year,
        for_date.month,
        for_date.day,
        int(settings.ORACLE_ASIA_END_HOUR),
        0,
        tzinfo=UK_TZ,
    )
    asia_start_utc = _as_utc(asia_start_local)
    asia_end_utc = _as_utc(asia_end_local)

    m15_rows = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M15",
            MT5Candle.time_utc >= asia_start_utc,
            MT5Candle.time_utc <= now_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )
    if not m15_rows:
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=for_date,
            for_date=for_date,
            timeframe="M15",
            as_of_utc=now_utc,
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="PRELIM",
            permission_source="ASIA",
            official=False,
            reason="No M15 data available for Asia prelim window.",
            spread=None,
            volatility=None,
            is_extreme=False,
            confidence=0.0,
            reasons=["asia_data_missing"],
            factors_json={
                "for_date": for_date.isoformat(),
                "asia_start_utc": asia_start_utc.isoformat(),
                "asia_end_utc": asia_end_utc.isoformat(),
                "missing_data": True,
            },
        )

    asia_rows = [row for row in m15_rows if asia_start_utc <= _as_utc(row.time_utc) < asia_end_utc]
    if not asia_rows:
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=for_date,
            for_date=for_date,
            timeframe="M15",
            as_of_utc=now_utc,
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="PRELIM",
            permission_source="ASIA",
            official=False,
            reason="Asia window candles are not available yet.",
            spread=None,
            volatility=None,
            is_extreme=False,
            confidence=0.0,
            reasons=["asia_window_missing"],
            factors_json={
                "for_date": for_date.isoformat(),
                "asia_start_utc": asia_start_utc.isoformat(),
                "asia_end_utc": asia_end_utc.isoformat(),
                "missing_data": True,
            },
        )

    asia_high = max(float(c.high) for c in asia_rows)
    asia_low = min(float(c.low) for c in asia_rows)
    post_rows = [row for row in m15_rows if _as_utc(row.time_utc) >= asia_end_utc]

    first_sweep_side: str | None = None
    first_sweep_level: float | None = None
    first_sweep_time: str | None = None
    for row in post_rows:
        row_time = _as_utc(row.time_utc)
        high = float(row.high)
        low = float(row.low)
        hit_high = high >= asia_high
        hit_low = low <= asia_low
        if not hit_high and not hit_low:
            continue
        if hit_high and hit_low:
            if abs(float(row.close) - asia_high) <= abs(float(row.close) - asia_low):
                first_sweep_side = "buyside"
                first_sweep_level = asia_high
            else:
                first_sweep_side = "sellside"
                first_sweep_level = asia_low
        elif hit_high:
            first_sweep_side = "buyside"
            first_sweep_level = asia_high
        else:
            first_sweep_side = "sellside"
            first_sweep_level = asia_low
        first_sweep_time = row_time.isoformat()
        break

    last = m15_rows[-1]
    lookback = m15_rows[:-1][-5:]
    prior_swing_high = max((float(c.high) for c in lookback), default=float(last.high))
    prior_swing_low = min((float(c.low) for c in lookback), default=float(last.low))
    last_close = float(last.close)
    bullish_displacement = last_close > prior_swing_high
    bearish_displacement = last_close < prior_swing_low

    volumes = [float(c.volume or 0.0) for c in m15_rows[-20:]]
    median_volume = float(median(volumes)) if volumes else 0.0
    last_volume = float(last.volume or 0.0)
    spike_mult = float(settings.ORACLE_ASIA_VOLUME_SPIKE_MULT or 1.8)
    volume_ratio = (last_volume / median_volume) if median_volume > 0 else None
    volume_spike = bool(volume_ratio is not None and volume_ratio >= spike_mult)

    reasons: list[str] = []
    if first_sweep_side:
        reasons.append(f"First sweep: {first_sweep_side}")
    else:
        reasons.append("No clear Asia sweep yet")
    if bullish_displacement:
        reasons.append("M15 bullish displacement above prior swing")
    if bearish_displacement:
        reasons.append("M15 bearish displacement below prior swing")
    if volume_spike:
        reasons.append(f"M15 volume spike ({volume_ratio:.2f}x median)")

    permission = "NO_TRADE"
    confidence = 0.45
    if first_sweep_side == "sellside" and bullish_displacement:
        permission = "BUY_ONLY"
        confidence = 0.72
    elif first_sweep_side == "buyside" and bearish_displacement:
        permission = "SELL_ONLY"
        confidence = 0.72
    elif bullish_displacement and not bearish_displacement:
        permission = "BUY_ONLY"
        confidence = 0.58
    elif bearish_displacement and not bullish_displacement:
        permission = "SELL_ONLY"
        confidence = 0.58
    else:
        reasons.append("No displacement confirmation")

    if volume_spike and permission in {"BUY_ONLY", "SELL_ONLY"}:
        confidence += 0.05
    if first_sweep_side is None:
        confidence -= 0.04
    confidence = round(_clamp(confidence, 0.05, 0.95), 4)

    if permission == "BUY_ONLY":
        reason = "Asia prelim bias is BUY_ONLY."
    elif permission == "SELL_ONLY":
        reason = "Asia prelim bias is SELL_ONLY."
    else:
        reason = "Asia prelim conditions are inconclusive."

    last_time_utc = _as_utc(last.time_utc)
    return DailyPermissionResult(
        symbol=symbol,
        date_uk=for_date,
        for_date=for_date,
        timeframe="M15",
        as_of_utc=min(last_time_utc, now_utc),
        computed_at_utc=now_utc,
        daily_permission=permission,
        daily_permission_stage="PRELIM",
        permission_source="ASIA",
        official=False,
        reason=reason,
        spread=None,
        volatility=round(_candle_range(last), 6),
        is_extreme=False,
        confidence=confidence,
        reasons=reasons,
        factors_json={
            "for_date": for_date.isoformat(),
            "asia_start_utc": asia_start_utc.isoformat(),
            "asia_end_utc": asia_end_utc.isoformat(),
            "asia_high": asia_high,
            "asia_low": asia_low,
            "first_sweep_side": first_sweep_side,
            "first_sweep_level": first_sweep_level,
            "first_sweep_time_utc": first_sweep_time,
            "bullish_displacement": bullish_displacement,
            "bearish_displacement": bearish_displacement,
            "prior_swing_high": prior_swing_high,
            "prior_swing_low": prior_swing_low,
            "volume_spike": volume_spike,
            "volume_ratio": volume_ratio,
            "volume_spike_mult": spike_mult,
            "confidence": confidence,
            "reasons": reasons,
        },
    )


def compute_daily_permission_from_m1(
    db: Session,
    *,
    symbol: str,
    ref_utc: datetime | None = None,
) -> DailyPermissionResult:
    now_utc = _as_utc(ref_utc or datetime.now(timezone.utc))
    if not UK_TZ_AVAILABLE:
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=_to_uk_date(now_utc),
            for_date=_to_uk_date(now_utc),
            timeframe="M1",
            as_of_utc=now_utc,
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="OFFICIAL",
            permission_source="LONDON_0801",
            official=True,
            reason="Europe/London timezone unavailable; 08:01 daily permission disabled.",
            spread=None,
            volatility=None,
            is_extreme=True,
            confidence=0.0,
            reasons=["timezone_unavailable"],
            factors_json={
                "timezone": "UTC_FALLBACK",
                "timezone_unavailable": True,
                "disable_0801_logic": True,
                "permission_date": _to_uk_date(now_utc).isoformat(),
                "permission_candle_close_utc": None,
                "permission_value": "NO_TRADE",
                "stale_reasons": ["tz_mismatch"],
            },
        )

    date_uk, _target_utc_unused = _daily_permission_target_utc(ref_utc=now_utc)
    window = TimeService.daily_permission_window_for_date(db, symbol=symbol, for_date_uk=date_uk)
    target_utc = window.target_london_0801_utc
    broker_offset_seconds = window.broker_offset_seconds
    target_broker_utc = window.expected_0801_broker_utc
    search_start_broker_utc = window.search_start_broker_utc
    search_end_broker_utc = window.search_end_broker_utc

    # Primary selection: DB candle timestamps are authoritative UTC.
    search_start_utc = target_utc - timedelta(minutes=3)
    search_end_utc = target_utc + timedelta(minutes=5) + timedelta(minutes=1)
    direct_candidates = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= search_start_utc,
            MT5Candle.time_utc < search_end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )

    # Fallback selection for legacy rows where broker-mapped timestamps were persisted.
    broker_candidates: list[MT5Candle] = []
    if not direct_candidates:
        broker_candidates = (
            db.query(MT5Candle)
            .filter(
                MT5Candle.symbol == symbol,
                MT5Candle.timeframe == "M1",
                MT5Candle.time_utc >= search_start_broker_utc,
                MT5Candle.time_utc < search_end_broker_utc,
            )
            .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
            .all()
        )

    m1_0801: MT5Candle | None = None
    selected_source = "none"
    if direct_candidates:
        m1_0801 = min(
            direct_candidates,
            key=lambda row: abs((_as_utc(row.time_utc) - target_utc).total_seconds()),
        )
        selected_source = "utc_window"
    elif broker_candidates:
        m1_0801 = min(
            broker_candidates,
            key=lambda row: abs((_as_utc(row.time_utc) - target_broker_utc).total_seconds()),
        )
        selected_source = "broker_window"
    logger.info(
        "daily permission 0801 lookup symbol=%s date_uk=%s target_utc=%s selection=%s utc_candidates=%s broker_candidates=%s",
        symbol,
        date_uk.isoformat(),
        target_utc.isoformat(),
        selected_source,
        len(direct_candidates),
        len(broker_candidates),
    )

    local_now = now_utc.astimezone(UK_TZ)
    target_local = target_utc.astimezone(UK_TZ)
    degraded_missing = (
        local_now.date() == target_local.date()
        and (local_now.hour, local_now.minute) >= (8, 20)
    )
    if not m1_0801:
        reason = "08:01 candle not available yet."
        if degraded_missing:
            reason = "08:01 candle not available yet (degraded after 08:20 London)."
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=date_uk,
            for_date=date_uk,
            timeframe="M1",
            as_of_utc=min(target_utc, now_utc),
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="OFFICIAL",
            permission_source="LONDON_0801",
            official=True,
            reason=reason,
            spread=None,
            volatility=None,
            is_extreme=True,
            confidence=0.0,
            reasons=["missing_0801"],
            factors_json={
                "target_utc": target_utc.isoformat(),
                "target_london": target_local.isoformat(),
                "expected_0801_broker_time": target_broker_utc.isoformat(),
                "broker_offset_seconds": broker_offset_seconds,
                "broker_offset_hours": round(float(broker_offset_seconds) / 3600.0, 4),
                "broker_server_time_utc": TimeService.latest_server_utc(db, symbol=symbol).isoformat(),
                "actual_candle_found_time": None,
                "selection_source": selected_source,
                "search_start_utc": search_start_utc.isoformat(),
                "search_end_utc": (search_end_utc - timedelta(minutes=1)).isoformat(),
                "search_start_broker_utc": search_start_broker_utc.isoformat(),
                "search_end_broker_utc": (search_end_broker_utc - timedelta(minutes=1)).isoformat(),
                "candidate_count_utc_window": len(direct_candidates),
                "candidate_count_broker_window": len(broker_candidates),
                "now_utc": now_utc.isoformat(),
                "now_london": local_now.isoformat(),
                "missing_data": True,
                "degraded": degraded_missing,
                "permission_date": date_uk.isoformat(),
                "permission_candle_close_utc": None,
                "permission_value": "NO_TRADE",
                "stale_reasons": ["missing_0801"],
            },
        )

    candle_as_of_raw = _as_utc(m1_0801.time_utc)
    if selected_source == "broker_window" and broker_offset_seconds:
        candle_as_of_utc = candle_as_of_raw - timedelta(seconds=broker_offset_seconds)
    else:
        candle_as_of_utc = candle_as_of_raw
    if candle_as_of_utc > (now_utc + timedelta(seconds=30)):
        return DailyPermissionResult(
            symbol=symbol,
            date_uk=date_uk,
            for_date=date_uk,
            timeframe="M1",
            as_of_utc=now_utc,
            computed_at_utc=now_utc,
            daily_permission="NO_TRADE",
            daily_permission_stage="OFFICIAL",
            permission_source="LONDON_0801",
            official=True,
            reason="08:01 candle timestamp is in the future; permission withheld.",
            spread=None,
            volatility=None,
            is_extreme=True,
            confidence=0.0,
            reasons=["future_timestamp"],
            factors_json={
                "target_utc": target_utc.isoformat(),
                "candle_time_utc": candle_as_of_utc.isoformat(),
                "actual_candle_found_time": candle_as_of_raw.isoformat(),
                "expected_0801_broker_time": target_broker_utc.isoformat(),
                "broker_offset_seconds": broker_offset_seconds,
                "broker_offset_hours": round(float(broker_offset_seconds) / 3600.0, 4),
                "broker_server_time_utc": TimeService.latest_server_utc(db, symbol=symbol).isoformat(),
                "selection_source": selected_source,
                "search_start_utc": search_start_utc.isoformat(),
                "search_end_utc": (search_end_utc - timedelta(minutes=1)).isoformat(),
                "search_start_broker_utc": search_start_broker_utc.isoformat(),
                "search_end_broker_utc": (search_end_broker_utc - timedelta(minutes=1)).isoformat(),
                "candidate_count_utc_window": len(direct_candidates),
                "candidate_count_broker_window": len(broker_candidates),
                "future_timestamp": True,
                "permission_date": date_uk.isoformat(),
                "permission_candle_close_utc": candle_as_of_utc.isoformat(),
                "permission_value": "NO_TRADE",
                "stale_reasons": ["tz_mismatch"],
            },
        )

    open_ = float(m1_0801.open)
    close = float(m1_0801.close)
    vol = _candle_range(m1_0801)

    recent = _latest_closed_candles(db, symbol=symbol, timeframe="M1", limit=80)
    baseline = median([_candle_range(c) for c in recent[-40:]]) if recent else 0.0
    extreme = bool(baseline > 0 and vol >= baseline * 3.0)

    if extreme:
        permission = "NO_TRADE"
        reason = "08:01 volatility extreme."
    elif close > open_:
        permission = "BUY_ONLY"
        reason = "08:01 M1 closed bullish."
    elif close < open_:
        permission = "SELL_ONLY"
        reason = "08:01 M1 closed bearish."
    else:
        permission = "NO_TRADE"
        reason = "08:01 M1 candle indecisive."

    return DailyPermissionResult(
        symbol=symbol,
        date_uk=date_uk,
        for_date=date_uk,
        timeframe="M1",
        as_of_utc=min(candle_as_of_utc, now_utc),
        computed_at_utc=now_utc,
        daily_permission=permission,
        daily_permission_stage="OFFICIAL",
        permission_source="LONDON_0801",
        official=True,
        reason=reason,
        spread=None,
        volatility=round(vol, 6),
        is_extreme=extreme,
        confidence=round(_clamp((0.52 if permission in {"BUY_ONLY", "SELL_ONLY"} else 0.35) + (0.1 if not extreme else -0.1), 0.05, 0.95), 4),
        reasons=[reason],
        factors_json={
            "target_utc": target_utc.isoformat(),
            "target_london": target_local.isoformat(),
            "candle_time_utc": candle_as_of_utc.isoformat(),
            "actual_candle_found_time": candle_as_of_raw.isoformat(),
            "expected_0801_broker_time": target_broker_utc.isoformat(),
            "broker_offset_seconds": broker_offset_seconds,
            "broker_offset_hours": round(float(broker_offset_seconds) / 3600.0, 4),
            "broker_server_time_utc": TimeService.latest_server_utc(db, symbol=symbol).isoformat(),
            "selection_source": selected_source,
            "search_start_utc": search_start_utc.isoformat(),
            "search_end_utc": (search_end_utc - timedelta(minutes=1)).isoformat(),
            "search_start_broker_utc": search_start_broker_utc.isoformat(),
            "search_end_broker_utc": (search_end_broker_utc - timedelta(minutes=1)).isoformat(),
            "candidate_count_utc_window": len(direct_candidates),
            "candidate_count_broker_window": len(broker_candidates),
            "open": open_,
            "close": close,
            "range": vol,
            "baseline_m1_range": baseline,
            "extreme_mult": 3.0,
            "permission_date": date_uk.isoformat(),
            "permission_candle_close_utc": candle_as_of_utc.isoformat(),
            "permission_value": permission,
            "stale_reasons": [],
        },
    )


def compute_opportunity_with_h1_confirmation(
    db: Session,
    *,
    symbol: str,
    daily_permission: str,
) -> OpportunityResult:
    m15 = _latest_closed_candles(db, symbol=symbol, timeframe="M15", limit=120)
    if not m15:
        raise ValueError("No closed M15 candles available")
    h1 = _latest_closed_candles(db, symbol=symbol, timeframe="H1", limit=80)
    if not h1:
        raise ValueError("No closed H1 candles available")

    last_m15 = m15[-1]
    prev_m15 = m15[-2] if len(m15) >= 2 else last_m15
    m15_close = float(last_m15.close)
    m15_open = float(last_m15.open)
    m15_ema5 = _ema([float(c.close) for c in m15[-40:]], period=5)

    # Opportunity layer: M15 setup candidate.
    buy_setup = m15_close > m15_open and m15_close > m15_ema5 and m15_close >= float(prev_m15.high)
    sell_setup = m15_close < m15_open and m15_close < m15_ema5 and m15_close <= float(prev_m15.low)
    if buy_setup:
        opp_dir = "BUY_ONLY"
    elif sell_setup:
        opp_dir = "SELL_ONLY"
    else:
        opp_dir = "NO_TRADE"

    last_h1 = h1[-1]
    h1_close = float(last_h1.close)
    h1_open = float(last_h1.open)
    h1_ema20 = _ema([float(c.close) for c in h1[-60:]], period=20)
    atr_h1_raw = _atr(h1[-20:], period=14)
    atr_h1 = round(atr_h1_raw, 4) if atr_h1_raw > 0 else None
    d1 = _latest_closed_candles(db, symbol=symbol, timeframe="D1", limit=30)
    d1_ranges = [max(float(c.high) - float(c.low), 0.0) for c in d1[-14:]] if d1 else []
    adr_d1 = round(sum(d1_ranges) / len(d1_ranges), 4) if d1_ranges else None
    risk_gate_pass = True
    if atr_h1 is not None and (atr_h1 < float(settings.ORACLE_ATR_H1_MIN) or atr_h1 > float(settings.ORACLE_ATR_H1_MAX)):
        risk_gate_pass = False
    if adr_d1 is not None and (adr_d1 < float(settings.ORACLE_ADR_D1_MIN) or adr_d1 > float(settings.ORACLE_ADR_D1_MAX)):
        risk_gate_pass = False
    h1_volumes = [float(c.volume or 0.0) for c in h1[-20:]]
    h1_volume = float(last_h1.volume or 0.0)
    h1_vol_median = float(median(h1_volumes)) if h1_volumes else 0.0
    h1_volume_ok = True if h1_vol_median <= 0 else h1_volume >= (h1_vol_median * 0.7)
    confirm_buy = h1_close > h1_open and h1_close >= h1_ema20
    confirm_sell = h1_close < h1_open and h1_close <= h1_ema20
    h1_structure_ok = (opp_dir == "BUY_ONLY" and confirm_buy) or (opp_dir == "SELL_ONLY" and confirm_sell)
    h1_confirm_ok = h1_structure_ok and h1_volume_ok

    aligned = opp_dir != "NO_TRADE" and daily_permission in {"BUY_ONLY", "SELL_ONLY"} and opp_dir == daily_permission

    if daily_permission == "NO_TRADE":
        final_allowed = "NO_TRADE"
        reason = "Daily permission is NO_TRADE."
    elif not aligned:
        final_allowed = "NO_TRADE"
        reason = "Opportunity rejected: direction conflicts with daily permission."
    elif not h1_confirm_ok:
        final_allowed = "NO_TRADE"
        if not h1_structure_ok:
            reason = "Opportunity rejected: H1 structure confirmation failed."
        else:
            reason = "Opportunity rejected: H1 volume filter failed."
    else:
        final_allowed = daily_permission
        reason = "Opportunity aligned with daily permission and H1 confirmation."

    m1_rows = _latest_closed_candles(db, symbol=symbol, timeframe="M1", limit=5)
    fast_bias_m1 = "NO_TRADE"
    fast_bias_m1_time_utc: str | None = None
    if m1_rows:
        m1_last = m1_rows[-1]
        m1_open = float(m1_last.open)
        m1_close = float(m1_last.close)
        if m1_close > m1_open:
            fast_bias_m1 = "BUY_ONLY"
        elif m1_close < m1_open:
            fast_bias_m1 = "SELL_ONLY"
        fast_bias_m1_time_utc = _as_utc(m1_last.time_utc).isoformat()

    micro_delta = 0.0
    if opp_dir in {"BUY_ONLY", "SELL_ONLY"}:
        if fast_bias_m1 == opp_dir:
            micro_delta = 0.03
        elif fast_bias_m1 in {"BUY_ONLY", "SELL_ONLY"} and fast_bias_m1 != opp_dir:
            micro_delta = -0.03

    body_ratio = abs(m15_close - m15_open) / max(_candle_range(last_m15), 1e-9)
    base_conf = 0.40 + min(max(body_ratio, 0.0), 1.0) * 0.40
    if aligned:
        base_conf += 0.10
    if h1_confirm_ok:
        base_conf += 0.08
    if not h1_volume_ok:
        base_conf -= 0.06
    as_of = _as_utc(last_m15.time_utc)
    as_of_uk = as_of.astimezone(UK_TZ)
    ny_context_active = 13 <= as_of_uk.hour < 17
    ny_delta = 0.0
    ny_note = "NY context neutral."
    if ny_context_active:
        if opp_dir in {"BUY_ONLY", "SELL_ONLY"} and opp_dir == daily_permission:
            ny_delta = 0.04
            ny_note = "NY momentum active and aligned with daily permission."
        elif (
            opp_dir in {"BUY_ONLY", "SELL_ONLY"}
            and daily_permission in {"BUY_ONLY", "SELL_ONLY"}
            and opp_dir != daily_permission
        ):
            ny_delta = -0.05
            ny_note = "NY conflict with daily permission. Caution."
        else:
            ny_note = "NY context active."

    base_conf += micro_delta + ny_delta
    confidence = round(_clamp(base_conf, 0.05, 0.99), 4)
    if final_allowed == "NO_TRADE":
        confidence = round(min(confidence, 0.55), 4)

    public_json = {
        "daily_permission": daily_permission,
        "opportunity_direction": opp_dir,
        "h1_confirm_ok": h1_confirm_ok,
        "aligned": aligned,
        "final_allowed": final_allowed,
        "signal_timeframe": "M15",
        "confirm_timeframe": "H1",
        "reason_basic": reason,
        "m15_close": m15_close,
        "m15_open": m15_open,
        "m15_ema5": round(m15_ema5, 6),
        "h1_close": h1_close,
        "h1_open": h1_open,
        "h1_ema20": round(h1_ema20, 6),
        "atr_h1": atr_h1,
        "adr_d1": adr_d1,
        "risk_gate_pass": risk_gate_pass,
        "news_gate_pass": True,
        "h1_volume_ok": h1_volume_ok,
        "h1_volume": round(h1_volume, 4),
        "h1_volume_median_20": round(h1_vol_median, 4),
        "fast_bias_m1": fast_bias_m1,
        "fast_bias_m1_time_utc": fast_bias_m1_time_utc,
        "micro_confidence_delta": round(micro_delta, 4),
        "ny_context_active": ny_context_active,
        "ny_context_label": "NY_1300_1700_LONDON" if ny_context_active else "OFF",
        "ny_confidence_delta": round(ny_delta, 4),
        "ny_note": ny_note,
    }
    internal_json = {
        **public_json,
        "as_of_utc": as_of.isoformat(),
        "m15_prev_high": float(prev_m15.high),
        "m15_prev_low": float(prev_m15.low),
    }

    return OpportunityResult(
        symbol=symbol,
        as_of_utc=as_of,
        timeframe_signal="M15",
        timeframe_confirm="H1",
        opportunity_direction=opp_dir,
        daily_permission=daily_permission,
        aligned=aligned,
        h1_confirm_ok=h1_confirm_ok,
        final_allowed=final_allowed,
        confidence=confidence,
        reason=reason,
        public_json=public_json,
        internal_json=internal_json,
    )
