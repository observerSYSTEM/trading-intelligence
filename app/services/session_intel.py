from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy.orm import Session

from app.core.symbols import SymbolMarketConfig, get_symbol_market_config
from app.core.time_utils import LONDON_TZ, as_utc
from app.db.models import MT5Candle

SessionState = Literal["asia", "london", "new_york", "off_session"]
AnchorClassification = Literal[
    "bullish_acceptance",
    "bearish_acceptance",
    "bullish_rejection",
    "bearish_rejection",
    "neutral",
]
AnchorBias = Literal["bullish", "bearish", "neutral"]
AnchorQuality = Literal["strong", "moderate", "weak"]
SweepSide = Literal["buy_side", "sell_side", "both"]
SweepType = Literal["rejection_sweep", "breakout", "double_sweep"]
MagnetBias = Literal["buyside", "sellside", "neutral"]
ZoneState = Literal["premium", "discount", "equilibrium"]
StructureState = Literal["bullish_mss", "bearish_mss", "bullish_bos", "bearish_bos", "none"]
StructureBias = Literal["bullish", "bearish", "neutral"]
FvgDirection = Literal["bullish", "bearish"]
FvgState = Literal["fresh", "partially_mitigated", "fully_mitigated", "expired", "none"]
SetupState = Literal["ready", "developing", "conflicted", "invalid", "none"]


def _time_in_window(current_time, *, start, end) -> bool:
    return start <= current_time <= end


def resolve_session_state(*, now_london: datetime, config: SymbolMarketConfig) -> SessionState:
    current_time = now_london.astimezone(LONDON_TZ).timetz().replace(tzinfo=None)
    if _time_in_window(current_time, start=config.asia_session.start, end=config.asia_session.end):
        return "asia"
    if _time_in_window(current_time, start=config.london_session.start, end=config.london_session.end):
        return "london"
    if _time_in_window(current_time, start=config.new_york_session.start, end=config.new_york_session.end):
        return "new_york"
    return "off_session"


def _asian_range_bounds_utc(*, now_london_value: datetime, config: SymbolMarketConfig) -> tuple[datetime, datetime]:
    local_day = now_london_value.astimezone(LONDON_TZ).date()
    start_local = datetime.combine(local_day, config.asia_session.start, tzinfo=LONDON_TZ)
    end_local = datetime.combine(local_day, config.london_session.start, tzinfo=LONDON_TZ)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _asian_range_candles(db: Session, *, symbol: str, start_utc: datetime, end_utc: datetime) -> list[MT5Candle]:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= start_utc,
            MT5Candle.time_utc < end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )


def _london_session_bounds_utc(*, now_london_value: datetime, config: SymbolMarketConfig) -> tuple[datetime, datetime]:
    local_day = now_london_value.astimezone(LONDON_TZ).date()
    start_local = datetime.combine(local_day, config.london_session.start, tzinfo=LONDON_TZ)
    end_local = datetime.combine(local_day, config.london_session.end, tzinfo=LONDON_TZ) + timedelta(minutes=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _london_session_candles(
    db: Session,
    *,
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[MT5Candle]:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= start_utc,
            MT5Candle.time_utc < end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )


def _session_timeframe_candles(
    db: Session,
    *,
    symbol: str,
    timeframe: str,
    start_utc: datetime,
    end_utc: datetime,
) -> list[MT5Candle]:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == timeframe,
            MT5Candle.time_utc >= start_utc,
            MT5Candle.time_utc < end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )


def _previous_day_bounds_utc(*, now_london_value: datetime) -> tuple[datetime, datetime]:
    previous_day = now_london_value.astimezone(LONDON_TZ).date() - timedelta(days=1)
    start_local = datetime.combine(previous_day, time(0, 0, 0), tzinfo=LONDON_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def _previous_day_candles(db: Session, *, symbol: str, start_utc: datetime, end_utc: datetime) -> list[MT5Candle]:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= start_utc,
            MT5Candle.time_utc < end_utc,
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .all()
    )


def _recent_h1_candles(
    db: Session,
    *,
    symbol: str,
    end_utc: datetime,
    lookback_bars: int,
) -> list[MT5Candle]:
    candles = (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "H1",
            MT5Candle.time_utc < end_utc,
        )
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(max(int(lookback_bars), 1) + 2)
        .all()
    )
    return list(reversed(candles))


def _latest_m1_candle(db: Session, *, symbol: str, end_utc: datetime) -> MT5Candle | None:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc <= end_utc,
        )
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )


def calculate_distance_pips(*, distance: float | None, config: SymbolMarketConfig) -> float | None:
    if distance is None or config.pip_size <= 0:
        return None
    return round(float(distance) / config.pip_size, 2)


def calculate_range_size_pips(*, high: float | None, low: float | None, config: SymbolMarketConfig) -> float | None:
    if high is None or low is None:
        return None
    return calculate_distance_pips(distance=float(high) - float(low), config=config)


def _anchor_target_times(*, now_london_value: datetime, config: SymbolMarketConfig) -> tuple[datetime, datetime]:
    local_day = now_london_value.astimezone(LONDON_TZ).date()
    anchor_london = datetime.combine(local_day, config.anchor.london_open_time, tzinfo=LONDON_TZ)
    return anchor_london, anchor_london.astimezone(timezone.utc)


def _anchor_candle(db: Session, *, symbol: str, anchor_utc: datetime) -> MT5Candle | None:
    return (
        db.query(MT5Candle)
        .filter(
            MT5Candle.symbol == symbol,
            MT5Candle.timeframe == "M1",
            MT5Candle.time_utc >= anchor_utc,
            MT5Candle.time_utc < anchor_utc + timedelta(minutes=1),
        )
        .order_by(MT5Candle.time_utc.asc(), MT5Candle.created_at.asc())
        .first()
    )


def calculate_candle_metrics(
    *,
    open_: float | None,
    high: float | None,
    low: float | None,
    close: float | None,
) -> dict:
    if None in {open_, high, low, close}:
        return {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "total_range": None,
            "body_size": None,
            "upper_wick": None,
            "lower_wick": None,
            "body_ratio": None,
            "wick_ratio": None,
            "direction": None,
        }

    total_range = max(float(high) - float(low), 0.0)
    body_size = abs(float(close) - float(open_))
    upper_wick = max(float(high) - max(float(open_), float(close)), 0.0)
    lower_wick = max(min(float(open_), float(close)) - float(low), 0.0)
    body_ratio = round(body_size / total_range, 4) if total_range > 0 else 0.0
    dominant_wick = max(upper_wick, lower_wick)
    wick_ratio = round(dominant_wick / total_range, 4) if total_range > 0 else 0.0
    direction: AnchorBias | None
    if float(close) > float(open_):
        direction = "bullish"
    elif float(close) < float(open_):
        direction = "bearish"
    else:
        direction = "neutral"

    return {
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "total_range": round(total_range, 5),
        "body_size": round(body_size, 5),
        "upper_wick": round(upper_wick, 5),
        "lower_wick": round(lower_wick, 5),
        "body_ratio": body_ratio,
        "wick_ratio": wick_ratio,
        "direction": direction,
    }


def _anchor_quality(
    *,
    classification: AnchorClassification | None,
    metrics: dict,
    config: SymbolMarketConfig,
) -> AnchorQuality:
    if classification in {"bullish_acceptance", "bearish_acceptance"}:
        if float(metrics.get("body_ratio") or 0.0) >= config.anchor.strong_body_ratio_min:
            return "strong"
        return "moderate"
    if classification in {"bullish_rejection", "bearish_rejection"}:
        if float(metrics.get("wick_ratio") or 0.0) >= config.anchor.strong_wick_ratio_min:
            return "strong"
        return "moderate"
    return "weak"


def classify_anchor_candle(*, metrics: dict, config: SymbolMarketConfig) -> dict:
    direction = str(metrics.get("direction") or "neutral")
    body_ratio = float(metrics.get("body_ratio") or 0.0)
    wick_ratio = float(metrics.get("wick_ratio") or 0.0)
    upper_wick = float(metrics.get("upper_wick") or 0.0)
    lower_wick = float(metrics.get("lower_wick") or 0.0)

    notes: list[str] = []
    classification: AnchorClassification = "neutral"

    if direction == "bullish" and body_ratio >= config.anchor.acceptance_body_ratio_min:
        classification = "bullish_acceptance"
        notes.append("bullish_body_ratio_met_acceptance_threshold")
    elif direction == "bearish" and body_ratio >= config.anchor.acceptance_body_ratio_min:
        classification = "bearish_acceptance"
        notes.append("bearish_body_ratio_met_acceptance_threshold")
    elif lower_wick > upper_wick and wick_ratio >= config.anchor.rejection_wick_ratio_min:
        classification = "bullish_rejection"
        notes.append("lower_wick_dominant_rejection_threshold_met")
    elif upper_wick > lower_wick and wick_ratio >= config.anchor.rejection_wick_ratio_min:
        classification = "bearish_rejection"
        notes.append("upper_wick_dominant_rejection_threshold_met")
    else:
        if direction == "neutral":
            notes.append("flat_close_neutral_direction")
        elif body_ratio < config.anchor.acceptance_body_ratio_min:
            notes.append("body_ratio_below_acceptance_threshold")
        if upper_wick == lower_wick:
            notes.append("no_dominant_wick")
        elif wick_ratio < config.anchor.rejection_wick_ratio_min:
            notes.append("dominant_wick_below_rejection_threshold")

    if classification.startswith("bullish"):
        bias: AnchorBias = "bullish"
    elif classification.startswith("bearish"):
        bias = "bearish"
    else:
        bias = "neutral"

    return {
        "anchor_classification": classification,
        "anchor_bias": bias,
        "anchor_quality": _anchor_quality(classification=classification, metrics=metrics, config=config),
        "anchor_notes": notes,
    }


def _close_inside_range(*, candle: MT5Candle, asian_high: float, asian_low: float) -> bool:
    close_value = float(candle.close)
    return asian_low <= close_value <= asian_high


def _sweep_quality(
    *,
    buffer_pips: float | None,
    returned_inside_range: bool,
    sweep_type: SweepType,
    config: SymbolMarketConfig,
    double_sweep_count: int = 1,
) -> AnchorQuality:
    moderate = config.sweep.quality.moderate_buffer_pips
    strong = config.sweep.quality.strong_buffer_pips
    buffer_value = float(buffer_pips or 0.0)
    if sweep_type == "double_sweep":
        if double_sweep_count >= 2 and (buffer_value >= moderate or returned_inside_range):
            return "strong"
        return "moderate"
    if buffer_value >= strong:
        return "strong"
    if returned_inside_range and buffer_value >= moderate:
        return "strong"
    if buffer_value >= moderate or returned_inside_range:
        return "moderate"
    return "weak"


def _detect_side_sweep(
    *,
    candles: list[MT5Candle],
    side: SweepSide,
    swept_level: float,
    asian_high: float,
    asian_low: float,
    config: SymbolMarketConfig,
) -> dict | None:
    for idx, candle in enumerate(candles):
        if side == "buy_side":
            buffer_pips = calculate_distance_pips(distance=float(candle.high) - swept_level, config=config)
        else:
            buffer_pips = calculate_distance_pips(distance=swept_level - float(candle.low), config=config)
        if buffer_pips is None or buffer_pips < config.sweep.minimum_buffer_pips:
            continue

        lookahead = candles[idx : idx + max(int(config.sweep.lookback_bars), 1)]
        returned_inside_range = any(
            _close_inside_range(candle=item, asian_high=asian_high, asian_low=asian_low)
            for item in lookahead
        )
        sweep_type: SweepType = "rejection_sweep" if returned_inside_range else "breakout"
        notes = [
            f"{side}_swept",
            f"buffer_pips={buffer_pips:.2f}",
            "returned_inside_range" if returned_inside_range else "remained_outside_range",
        ]
        return {
            "sweep_side": side,
            "sweep_type": sweep_type,
            "swept_level": swept_level,
            "sweep_extreme": _round_level(candle.high if side == "buy_side" else candle.low),
            "reference_sweep_side": side,
            "sweep_buffer_pips": buffer_pips,
            "sweep_time_utc": as_utc(candle.time_utc).isoformat(),
            "sweep_time_london": as_utc(candle.time_utc).astimezone(LONDON_TZ).isoformat(),
            "returned_inside_range": returned_inside_range,
            "sweep_quality": _sweep_quality(
                buffer_pips=buffer_pips,
                returned_inside_range=returned_inside_range,
                sweep_type=sweep_type,
                config=config,
            ),
            "sweep_notes": notes,
        }
    return None


def detect_london_sweep(
    *,
    candles: list[MT5Candle],
    asian_high: float | None,
    asian_low: float | None,
    config: SymbolMarketConfig,
) -> dict:
    if asian_high is None or asian_low is None:
        return {
            "sweep_available": False,
            "sweep_side": None,
            "sweep_type": None,
            "swept_level": None,
            "sweep_extreme": None,
            "reference_sweep_side": None,
            "sweep_buffer_pips": None,
            "sweep_time_london": None,
            "sweep_time_utc": None,
            "returned_inside_range": None,
            "sweep_quality": None,
            "sweep_notes": ["asian_range_missing"],
        }
    if not candles:
        return {
            "sweep_available": False,
            "sweep_side": None,
            "sweep_type": None,
            "swept_level": None,
            "sweep_extreme": None,
            "reference_sweep_side": None,
            "sweep_buffer_pips": None,
            "sweep_time_london": None,
            "sweep_time_utc": None,
            "returned_inside_range": None,
            "sweep_quality": None,
            "sweep_notes": ["no_london_session_candles"],
        }

    buy_event = _detect_side_sweep(
        candles=candles,
        side="buy_side",
        swept_level=asian_high,
        asian_high=asian_high,
        asian_low=asian_low,
        config=config,
    )
    sell_event = _detect_side_sweep(
        candles=candles,
        side="sell_side",
        swept_level=asian_low,
        asian_high=asian_high,
        asian_low=asian_low,
        config=config,
    )

    if buy_event and sell_event:
        latest = max((buy_event, sell_event), key=lambda item: item["sweep_time_utc"])
        combined_notes = [
            "double_sweep_detected",
            f"buy_side_type={buy_event['sweep_type']}",
            f"sell_side_type={sell_event['sweep_type']}",
        ]
        return {
            "sweep_available": True,
            "sweep_side": "both",
            "sweep_type": "double_sweep",
            "swept_level": latest["swept_level"],
            "sweep_extreme": latest["sweep_extreme"],
            "reference_sweep_side": latest["sweep_side"],
            "sweep_buffer_pips": latest["sweep_buffer_pips"],
            "sweep_time_london": latest["sweep_time_london"],
            "sweep_time_utc": latest["sweep_time_utc"],
            "returned_inside_range": latest["returned_inside_range"],
            "sweep_quality": _sweep_quality(
                buffer_pips=max(buy_event["sweep_buffer_pips"], sell_event["sweep_buffer_pips"]),
                returned_inside_range=bool(buy_event["returned_inside_range"]) or bool(sell_event["returned_inside_range"]),
                sweep_type="double_sweep",
                config=config,
                double_sweep_count=2,
            ),
            "sweep_notes": combined_notes,
        }
    if buy_event:
        return {"sweep_available": True, **buy_event}
    if sell_event:
        return {"sweep_available": True, **sell_event}
    return {
        "sweep_available": False,
        "sweep_side": None,
        "sweep_type": None,
        "swept_level": None,
        "sweep_extreme": None,
        "reference_sweep_side": None,
        "sweep_buffer_pips": None,
        "sweep_time_london": None,
        "sweep_time_utc": None,
        "returned_inside_range": None,
        "sweep_quality": None,
        "sweep_notes": ["no_london_sweep_detected"],
    }


def _round_level(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 5)


def _add_candidate(
    *,
    candidates: dict[tuple[str, int | float], dict],
    side: str,
    candidate_type: str,
    level: float | None,
    config: SymbolMarketConfig,
) -> None:
    if level is None:
        return
    level_value = _round_level(level)
    if level_value is None:
        return
    if config.pip_size > 0:
        level_key: int | float = int(round(level_value / config.pip_size))
    else:
        level_key = level_value
    candidates.setdefault(
        (side, level_key),
        {
            "side": side,
            "type": candidate_type,
            "level": level_value,
        },
    )


def _extract_h1_swing_levels(*, candles: list[MT5Candle]) -> dict[str, list[float]]:
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for idx in range(1, max(len(candles) - 1, 1)):
        if idx >= len(candles) - 1:
            break
        previous_candle = candles[idx - 1]
        current_candle = candles[idx]
        next_candle = candles[idx + 1]
        if float(current_candle.high) > float(previous_candle.high) and float(current_candle.high) > float(next_candle.high):
            swing_highs.append(_round_level(current_candle.high))
        if float(current_candle.low) < float(previous_candle.low) and float(current_candle.low) < float(next_candle.low):
            swing_lows.append(_round_level(current_candle.low))
    return {
        "highs": [level for level in swing_highs if level is not None],
        "lows": [level for level in swing_lows if level is not None],
    }


def _round_number_targets(*, current_price: float | None, interval: float) -> dict[str, list[float]]:
    if current_price is None or interval <= 0:
        return {"buyside": [], "sellside": []}

    above: list[float] = []
    below: list[float] = []
    current_step = math.floor(float(current_price) / interval)
    for offset in range(1, 4):
        above.append(_round_level((current_step + offset) * interval))
        below.append(_round_level((current_step - offset + 1) * interval))
    return {
        "buyside": [level for level in above if level is not None and level > float(current_price)],
        "sellside": [level for level in below if level is not None and level < float(current_price)],
    }


def _select_next_liquidity(
    *,
    candidates: list[dict],
    side: str,
    current_price: float | None,
    config: SymbolMarketConfig,
) -> dict | None:
    if current_price is None:
        return None

    ranking = config.magnet.ranking.buyside if side == "buyside" else config.magnet.ranking.sellside
    ranking_index = {name: idx for idx, name in enumerate(ranking)}
    eligible: list[dict] = []
    for candidate in candidates:
        if candidate["side"] != side:
            continue
        level = float(candidate["level"])
        if side == "buyside" and level <= float(current_price):
            continue
        if side == "sellside" and level >= float(current_price):
            continue
        eligible.append(
            {
                **candidate,
                "distance_pips": calculate_distance_pips(distance=abs(level - float(current_price)), config=config),
            }
        )

    if not eligible:
        return None

    eligible.sort(
        key=lambda item: (
            ranking_index.get(str(item["type"]), len(ranking_index)),
            float(item.get("distance_pips") or 0.0),
            float(item["level"]) if side == "buyside" else -float(item["level"]),
        )
    )
    return eligible[0]


def _build_liquidity_targets(
    *,
    current_price: float | None,
    asian_high: float | None,
    asian_low: float | None,
    london_candles: list[MT5Candle],
    previous_day_candles: list[MT5Candle],
    h1_candles: list[MT5Candle],
    config: SymbolMarketConfig,
) -> dict:
    candidate_map: dict[tuple[str, int | float], dict] = {}
    previous_day_high = max((float(candle.high) for candle in previous_day_candles), default=None)
    previous_day_low = min((float(candle.low) for candle in previous_day_candles), default=None)
    london_high = max((float(candle.high) for candle in london_candles), default=None)
    london_low = min((float(candle.low) for candle in london_candles), default=None)
    h1_swings = _extract_h1_swing_levels(candles=h1_candles)
    round_numbers = _round_number_targets(
        current_price=current_price,
        interval=config.magnet.round_number_interval,
    )

    _add_candidate(candidates=candidate_map, side="buyside", candidate_type="pdh", level=previous_day_high, config=config)
    _add_candidate(candidates=candidate_map, side="sellside", candidate_type="pdl", level=previous_day_low, config=config)
    _add_candidate(candidates=candidate_map, side="buyside", candidate_type="asian_high", level=asian_high, config=config)
    _add_candidate(candidates=candidate_map, side="sellside", candidate_type="asian_low", level=asian_low, config=config)
    _add_candidate(candidates=candidate_map, side="buyside", candidate_type="london_high", level=london_high, config=config)
    _add_candidate(candidates=candidate_map, side="sellside", candidate_type="london_low", level=london_low, config=config)
    for level in h1_swings["highs"]:
        _add_candidate(candidates=candidate_map, side="buyside", candidate_type="h1_swing_high", level=level, config=config)
    for level in h1_swings["lows"]:
        _add_candidate(candidates=candidate_map, side="sellside", candidate_type="h1_swing_low", level=level, config=config)
    for level in round_numbers["buyside"]:
        _add_candidate(candidates=candidate_map, side="buyside", candidate_type="round_number", level=level, config=config)
    for level in round_numbers["sellside"]:
        _add_candidate(candidates=candidate_map, side="sellside", candidate_type="round_number", level=level, config=config)

    candidates = list(candidate_map.values())
    next_buyside = _select_next_liquidity(
        candidates=candidates,
        side="buyside",
        current_price=current_price,
        config=config,
    )
    next_sellside = _select_next_liquidity(
        candidates=candidates,
        side="sellside",
        current_price=current_price,
        config=config,
    )
    notes = [
        f"candidate_count={len(candidates)}",
        f"h1_swing_highs={len(h1_swings['highs'])}",
        f"h1_swing_lows={len(h1_swings['lows'])}",
    ]
    return {
        "previous_day_high": _round_level(previous_day_high),
        "previous_day_low": _round_level(previous_day_low),
        "london_high": _round_level(london_high),
        "london_low": _round_level(london_low),
        "next_buyside": next_buyside,
        "next_sellside": next_sellside,
        "notes": notes,
    }


def _derive_dealing_range(
    *,
    asian_high: float | None,
    asian_low: float | None,
    sweep_summary: dict,
    config: SymbolMarketConfig,
) -> dict:
    if asian_high is None or asian_low is None:
        return {
            "dealing_range_high": None,
            "dealing_range_low": None,
            "equilibrium": None,
            "zone_notes": ["asian_range_missing"],
        }

    dealing_high = float(asian_high)
    dealing_low = float(asian_low)
    zone_notes: list[str] = ["pre_sweep_uses_asian_range"]

    if bool(sweep_summary.get("sweep_available")):
        reference_side = str(sweep_summary.get("reference_sweep_side") or sweep_summary.get("sweep_side") or "")
        sweep_extreme = sweep_summary.get("sweep_extreme")
        if reference_side == "buy_side" and sweep_extreme is not None:
            dealing_high = max(float(sweep_extreme), float(asian_low))
            dealing_low = float(asian_low)
            zone_notes = ["post_sweep_uses_buyside_extreme_to_asian_low"]
        elif reference_side == "sell_side" and sweep_extreme is not None:
            dealing_high = float(asian_high)
            dealing_low = min(float(sweep_extreme), float(asian_high))
            zone_notes = ["post_sweep_uses_sellside_extreme_to_asian_high"]

    equilibrium = (dealing_high + dealing_low) / 2.0
    return {
        "dealing_range_high": _round_level(dealing_high),
        "dealing_range_low": _round_level(dealing_low),
        "equilibrium": _round_level(equilibrium),
        "zone_notes": zone_notes,
    }


def _resolve_zone_state(
    *,
    current_price: float | None,
    dealing_range_high: float | None,
    dealing_range_low: float | None,
    equilibrium: float | None,
    config: SymbolMarketConfig,
) -> dict:
    if current_price is None or dealing_range_high is None or dealing_range_low is None or equilibrium is None:
        return {
            "zone_state": None,
            "distance_from_equilibrium_pips": None,
            "zone_notes": ["zone_state_unavailable"],
        }

    if float(current_price) > float(equilibrium):
        zone_state: ZoneState = "premium"
    elif float(current_price) < float(equilibrium):
        zone_state = "discount"
    else:
        zone_state = "equilibrium"

    return {
        "zone_state": zone_state,
        "distance_from_equilibrium_pips": calculate_distance_pips(
            distance=abs(float(current_price) - float(equilibrium)),
            config=config,
        ),
        "zone_notes": [f"current_price_in_{zone_state}"],
    }


def _resolve_active_magnet(
    *,
    current_price: float | None,
    next_buyside: dict | None,
    next_sellside: dict | None,
    zone_state: str | None,
    sweep_summary: dict,
    anchor_summary: dict,
    config: SymbolMarketConfig,
) -> dict:
    chosen: dict | None = None
    magnet_bias: MagnetBias = "neutral"
    notes: list[str] = []

    if zone_state == "premium" and next_sellside is not None:
        chosen = next_sellside
        magnet_bias = "sellside"
        notes.append("premium_zone_prefers_sellside_liquidity")
    elif zone_state == "discount" and next_buyside is not None:
        chosen = next_buyside
        magnet_bias = "buyside"
        notes.append("discount_zone_prefers_buyside_liquidity")
    else:
        sweep_side = str(sweep_summary.get("reference_sweep_side") or sweep_summary.get("sweep_side") or "")
        if sweep_side == "buy_side" and next_sellside is not None:
            chosen = next_sellside
            magnet_bias = "sellside"
            notes.append("buyside_sweep_biases_toward_sellside")
        elif sweep_side == "sell_side" and next_buyside is not None:
            chosen = next_buyside
            magnet_bias = "buyside"
            notes.append("sellside_sweep_biases_toward_buyside")
        elif str(anchor_summary.get("anchor_bias")) == "bullish" and next_buyside is not None:
            chosen = next_buyside
            magnet_bias = "buyside"
            notes.append("bullish_anchor_bias_prefers_buyside")
        elif str(anchor_summary.get("anchor_bias")) == "bearish" and next_sellside is not None:
            chosen = next_sellside
            magnet_bias = "sellside"
            notes.append("bearish_anchor_bias_prefers_sellside")

    if chosen is None:
        available = [candidate for candidate in (next_buyside, next_sellside) if candidate is not None]
        if available:
            chosen = min(available, key=lambda item: float(item.get("distance_pips") or 0.0))
            magnet_bias = "buyside" if chosen["side"] == "buyside" else "sellside"
            notes.append("nearest_ranked_liquidity_selected")

    if chosen is None:
        return {
            "magnet_bias": magnet_bias,
            "active_magnet_level": None,
            "active_magnet_type": None,
            "distance_to_magnet_pips": None,
            "magnet_notes": notes + ["no_active_magnet"],
        }

    notes.append(f"active_type={chosen['type']}")
    return {
        "magnet_bias": magnet_bias,
        "active_magnet_level": chosen["level"],
        "active_magnet_type": chosen["type"],
        "distance_to_magnet_pips": calculate_distance_pips(
            distance=abs(float(chosen["level"]) - float(current_price or chosen["level"])),
            config=config,
        ),
        "magnet_notes": notes,
    }


def _extract_structure_swings(*, candles: list[MT5Candle], timeframe: str) -> list[dict]:
    swings: list[dict] = []
    if len(candles) < 3:
        return swings

    for idx in range(1, len(candles) - 1):
        previous_candle = candles[idx - 1]
        current_candle = candles[idx]
        next_candle = candles[idx + 1]
        current_time_utc = as_utc(current_candle.time_utc)
        if float(current_candle.high) > float(previous_candle.high) and float(current_candle.high) > float(next_candle.high):
            swings.append(
                {
                    "kind": "high",
                    "level": _round_level(current_candle.high),
                    "time_utc": current_time_utc,
                    "timeframe": timeframe,
                }
            )
        if float(current_candle.low) < float(previous_candle.low) and float(current_candle.low) < float(next_candle.low):
            swings.append(
                {
                    "kind": "low",
                    "level": _round_level(current_candle.low),
                    "time_utc": current_time_utc,
                    "timeframe": timeframe,
                }
            )
    return swings


def _collect_recent_structure_swings(
    *,
    london_m1_candles: list[MT5Candle],
    london_m5_candles: list[MT5Candle],
    config: SymbolMarketConfig,
) -> dict[str, list[dict]]:
    lookback = max(int(config.structure.swing_lookback), 1)
    raw_swings = _extract_structure_swings(candles=london_m1_candles, timeframe="M1") + _extract_structure_swings(
        candles=london_m5_candles,
        timeframe="M5",
    )
    swing_highs = sorted((item for item in raw_swings if item["kind"] == "high"), key=lambda item: item["time_utc"])
    swing_lows = sorted((item for item in raw_swings if item["kind"] == "low"), key=lambda item: item["time_utc"])
    return {
        "highs": swing_highs[-lookback:],
        "lows": swing_lows[-lookback:],
    }


def _structure_displacement_quality(*, displacement_size_pips: float | None, config: SymbolMarketConfig) -> AnchorQuality:
    displacement_value = float(displacement_size_pips or 0.0)
    if displacement_value >= config.structure.quality.strong_displacement_pips:
        return "strong"
    if displacement_value >= config.structure.quality.moderate_displacement_pips:
        return "moderate"
    return "weak"


def _confirm_structure_break(
    *,
    london_m1_candles: list[MT5Candle],
    swings: list[dict],
    direction: StructureBias,
    config: SymbolMarketConfig,
) -> dict | None:
    if not london_m1_candles or not swings:
        return None

    swing_kind = "high" if direction == "bullish" else "low"
    relevant_swings = [item for item in swings if item["kind"] == swing_kind]
    if not relevant_swings:
        return None

    for candle in london_m1_candles:
        candle_time_utc = as_utc(candle.time_utc)
        prior_swings = [item for item in relevant_swings if item["time_utc"] < candle_time_utc]
        if not prior_swings:
            continue
        break_level = float(prior_swings[-1]["level"])
        close_value = float(candle.close)
        displacement = (
            calculate_distance_pips(distance=close_value - break_level, config=config)
            if direction == "bullish"
            else calculate_distance_pips(distance=break_level - close_value, config=config)
        )
        close_beyond_level = close_value > break_level if direction == "bullish" else close_value < break_level
        if not close_beyond_level:
            continue
        if displacement is None or displacement < config.structure.minimum_displacement_pips:
            continue

        return {
            "direction": direction,
            "break_level": _round_level(break_level),
            "break_time_dt": candle_time_utc,
            "break_time_utc": candle_time_utc.isoformat(),
            "break_time_london": candle_time_utc.astimezone(LONDON_TZ).isoformat(),
            "displacement_size_pips": displacement,
            "displacement_quality": _structure_displacement_quality(
                displacement_size_pips=displacement,
                config=config,
            ),
            "structure_notes": [
                f"confirmed_with_{config.structure.break_confirmation_method}",
                f"source_swing_{swing_kind}_{str(prior_swings[-1]['timeframe']).lower()}",
            ],
        }
    return None


def detect_london_structure(
    *,
    london_m1_candles: list[MT5Candle],
    london_m5_candles: list[MT5Candle],
    sweep_summary: dict,
    zone_state: str | None,
    config: SymbolMarketConfig,
) -> dict:
    swings = _collect_recent_structure_swings(
        london_m1_candles=london_m1_candles,
        london_m5_candles=london_m5_candles,
        config=config,
    )
    swing_highs = swings["highs"]
    swing_lows = swings["lows"]
    structure_available = bool(london_m1_candles) and bool(swing_highs or swing_lows)
    if not structure_available:
        return {
            "structure_available": False,
            "structure_state": "none",
            "structure_bias": "neutral",
            "mss_detected": False,
            "bos_detected": False,
            "break_level": None,
            "break_time_dt": None,
            "break_time_london": None,
            "break_time_utc": None,
            "displacement_size_pips": None,
            "displacement_quality": None,
            "structure_notes": ["insufficient_structure_swings"],
        }

    bullish_break = _confirm_structure_break(
        london_m1_candles=london_m1_candles,
        swings=swing_highs,
        direction="bullish",
        config=config,
    )
    bearish_break = _confirm_structure_break(
        london_m1_candles=london_m1_candles,
        swings=swing_lows,
        direction="bearish",
        config=config,
    )

    candidate_breaks = [item for item in (bullish_break, bearish_break) if item is not None]
    if not candidate_breaks:
        return {
            "structure_available": True,
            "structure_state": "none",
            "structure_bias": "neutral",
            "mss_detected": False,
            "bos_detected": False,
            "break_level": None,
            "break_time_dt": None,
            "break_time_london": None,
            "break_time_utc": None,
            "displacement_size_pips": None,
            "displacement_quality": None,
            "structure_notes": [
                "no_valid_break_confirmed",
                f"recent_swing_highs={len(swing_highs)}",
                f"recent_swing_lows={len(swing_lows)}",
            ],
        }

    selected_break = max(
        candidate_breaks,
        key=lambda item: (item["break_time_dt"], float(item["displacement_size_pips"] or 0.0)),
    )
    reference_sweep_side = str(sweep_summary.get("reference_sweep_side") or sweep_summary.get("sweep_side") or "")
    bullish_reversal_context = reference_sweep_side == "sell_side" or zone_state == "discount"
    bearish_reversal_context = reference_sweep_side == "buy_side" or zone_state == "premium"

    structure_state: StructureState
    structure_bias: StructureBias
    if selected_break["direction"] == "bullish":
        structure_state = "bullish_mss" if bullish_reversal_context else "bullish_bos"
        structure_bias = "bullish"
    else:
        structure_state = "bearish_mss" if bearish_reversal_context else "bearish_bos"
        structure_bias = "bearish"

    return {
        "structure_available": True,
        "structure_state": structure_state,
        "structure_bias": structure_bias,
        "mss_detected": structure_state.endswith("_mss"),
        "bos_detected": structure_state.endswith("_bos"),
        "break_level": selected_break["break_level"],
        "break_time_dt": selected_break["break_time_dt"],
        "break_time_london": selected_break["break_time_london"],
        "break_time_utc": selected_break["break_time_utc"],
        "displacement_size_pips": selected_break["displacement_size_pips"],
        "displacement_quality": selected_break["displacement_quality"],
        "structure_notes": selected_break["structure_notes"]
        + [
            f"reference_sweep_side={reference_sweep_side or 'none'}",
            f"zone_state={zone_state or 'none'}",
            f"recent_swing_highs={len(swing_highs)}",
            f"recent_swing_lows={len(swing_lows)}",
        ],
    }


def _fvg_quality(*, gap_size_pips: float | None, config: SymbolMarketConfig) -> AnchorQuality:
    gap_value = float(gap_size_pips or 0.0)
    if gap_value >= config.fvg.quality.strong_gap_pips:
        return "strong"
    if gap_value >= config.fvg.quality.moderate_gap_pips:
        return "moderate"
    return "weak"


def _resolve_fvg_state(
    *,
    direction: FvgDirection,
    fvg_low: float,
    fvg_high: float,
    later_candles: list[MT5Candle],
    age_bars: int,
    config: SymbolMarketConfig,
) -> dict:
    if age_bars > config.fvg.maximum_age_bars:
        return {
            "fvg_state": "expired",
            "fvg_mitigated": False,
            "mitigation_ratio": 0.0,
            "fvg_notes": [f"expired_after_{age_bars}_bars"],
        }

    gap_size = max(float(fvg_high) - float(fvg_low), 0.0)
    if gap_size <= 0:
        return {
            "fvg_state": "none",
            "fvg_mitigated": False,
            "mitigation_ratio": 0.0,
            "fvg_notes": ["invalid_gap_bounds"],
        }

    penetration = 0.0
    for candle in later_candles:
        if direction == "bullish":
            if float(candle.low) < float(fvg_high):
                penetration = max(penetration, float(fvg_high) - max(float(candle.low), float(fvg_low)))
        else:
            if float(candle.high) > float(fvg_low):
                penetration = max(penetration, min(float(candle.high), float(fvg_high)) - float(fvg_low))

    mitigation_ratio = max(min(penetration / gap_size, 1.0), 0.0)
    if mitigation_ratio >= config.fvg.mitigation.full_fill_ratio:
        return {
            "fvg_state": "fully_mitigated",
            "fvg_mitigated": True,
            "mitigation_ratio": round(mitigation_ratio, 4),
            "fvg_notes": [f"mitigation_ratio={mitigation_ratio:.4f}", "full_mitigation_detected"],
        }
    if mitigation_ratio >= config.fvg.mitigation.partial_fill_ratio:
        return {
            "fvg_state": "partially_mitigated",
            "fvg_mitigated": True,
            "mitigation_ratio": round(mitigation_ratio, 4),
            "fvg_notes": [f"mitigation_ratio={mitigation_ratio:.4f}", "partial_mitigation_detected"],
        }
    return {
        "fvg_state": "fresh",
        "fvg_mitigated": False,
        "mitigation_ratio": round(mitigation_ratio, 4),
        "fvg_notes": [f"mitigation_ratio={mitigation_ratio:.4f}", "unmitigated_gap"],
    }


def detect_london_fvg(
    *,
    london_m1_candles: list[MT5Candle],
    structure_summary: dict,
    magnet_bias: str | None,
    config: SymbolMarketConfig,
) -> dict:
    break_time_dt = structure_summary.get("break_time_dt")
    structure_bias = str(structure_summary.get("structure_bias") or "neutral")
    if not structure_summary.get("structure_available") or structure_bias not in {"bullish", "bearish"} or break_time_dt is None:
        return {
            "fvg_available": False,
            "fvg_direction": None,
            "fvg_state": "none",
            "fvg_high": None,
            "fvg_low": None,
            "fvg_mid": None,
            "fvg_size_pips": None,
            "fvg_created_time_london": None,
            "fvg_created_time_utc": None,
            "fvg_age_bars": None,
            "fvg_mitigated": False,
            "fvg_quality": None,
            "fvg_notes": ["structure_confirmation_required"],
        }

    candidate_rows: list[dict] = []
    for idx in range(2, len(london_m1_candles)):
        candle_one = london_m1_candles[idx - 2]
        candle_two = london_m1_candles[idx - 1]
        candle_three = london_m1_candles[idx]
        del candle_two
        first_time = as_utc(candle_one.time_utc)
        created_time = as_utc(candle_three.time_utc)
        if first_time <= break_time_dt:
            continue

        bullish_gap = float(candle_three.low) - float(candle_one.high)
        bearish_gap = float(candle_one.low) - float(candle_three.high)
        later_candles = london_m1_candles[idx + 1 :]
        age_bars = len(later_candles)

        if bullish_gap > 0:
            gap_size_pips = calculate_distance_pips(distance=bullish_gap, config=config)
            if gap_size_pips is not None and gap_size_pips >= config.fvg.minimum_gap_size_pips:
                fvg_low = float(candle_one.high)
                fvg_high = float(candle_three.low)
                state_summary = _resolve_fvg_state(
                    direction="bullish",
                    fvg_low=fvg_low,
                    fvg_high=fvg_high,
                    later_candles=later_candles,
                    age_bars=age_bars,
                    config=config,
                )
                candidate_rows.append(
                    {
                        "fvg_direction": "bullish",
                        "fvg_state": state_summary["fvg_state"],
                        "fvg_high": _round_level(fvg_high),
                        "fvg_low": _round_level(fvg_low),
                        "fvg_mid": _round_level((fvg_high + fvg_low) / 2.0),
                        "fvg_size_pips": gap_size_pips,
                        "fvg_created_time_london": created_time.astimezone(LONDON_TZ).isoformat(),
                        "fvg_created_time_utc": created_time.isoformat(),
                        "fvg_created_time_dt": created_time,
                        "fvg_age_bars": age_bars,
                        "fvg_mitigated": state_summary["fvg_mitigated"],
                        "fvg_quality": _fvg_quality(gap_size_pips=gap_size_pips, config=config),
                        "fvg_notes": state_summary["fvg_notes"] + ["bullish_three_candle_imbalance"],
                    }
                )

        if bearish_gap > 0:
            gap_size_pips = calculate_distance_pips(distance=bearish_gap, config=config)
            if gap_size_pips is not None and gap_size_pips >= config.fvg.minimum_gap_size_pips:
                fvg_low = float(candle_three.high)
                fvg_high = float(candle_one.low)
                state_summary = _resolve_fvg_state(
                    direction="bearish",
                    fvg_low=fvg_low,
                    fvg_high=fvg_high,
                    later_candles=later_candles,
                    age_bars=age_bars,
                    config=config,
                )
                candidate_rows.append(
                    {
                        "fvg_direction": "bearish",
                        "fvg_state": state_summary["fvg_state"],
                        "fvg_high": _round_level(fvg_high),
                        "fvg_low": _round_level(fvg_low),
                        "fvg_mid": _round_level((fvg_high + fvg_low) / 2.0),
                        "fvg_size_pips": gap_size_pips,
                        "fvg_created_time_london": created_time.astimezone(LONDON_TZ).isoformat(),
                        "fvg_created_time_utc": created_time.isoformat(),
                        "fvg_created_time_dt": created_time,
                        "fvg_age_bars": age_bars,
                        "fvg_mitigated": state_summary["fvg_mitigated"],
                        "fvg_quality": _fvg_quality(gap_size_pips=gap_size_pips, config=config),
                        "fvg_notes": state_summary["fvg_notes"] + ["bearish_three_candle_imbalance"],
                    }
                )

    if not candidate_rows:
        return {
            "fvg_available": False,
            "fvg_direction": None,
            "fvg_state": "none",
            "fvg_high": None,
            "fvg_low": None,
            "fvg_mid": None,
            "fvg_size_pips": None,
            "fvg_created_time_london": None,
            "fvg_created_time_utc": None,
            "fvg_age_bars": None,
            "fvg_mitigated": False,
            "fvg_quality": None,
            "fvg_notes": ["no_post_structure_fvg_detected"],
        }

    active_candidates = [item for item in candidate_rows if item["fvg_state"] != "expired"]
    if not active_candidates:
        return {
            "fvg_available": False,
            "fvg_direction": None,
            "fvg_state": "none",
            "fvg_high": None,
            "fvg_low": None,
            "fvg_mid": None,
            "fvg_size_pips": None,
            "fvg_created_time_london": None,
            "fvg_created_time_utc": None,
            "fvg_age_bars": None,
            "fvg_mitigated": False,
            "fvg_quality": None,
            "fvg_notes": ["all_post_structure_fvgs_expired"],
        }

    magnet_direction = "bullish" if magnet_bias == "buyside" else "bearish" if magnet_bias == "sellside" else "neutral"
    state_rank = {"fresh": 0, "partially_mitigated": 1, "fully_mitigated": 2, "expired": 3}
    active_candidates.sort(
        key=lambda item: (
            0 if item["fvg_direction"] == structure_bias else 1,
            0 if magnet_direction != "neutral" and item["fvg_direction"] == magnet_direction else 1,
            state_rank.get(str(item["fvg_state"]), 4),
            int(item["fvg_age_bars"] or 0),
            -float(item["fvg_size_pips"] or 0.0),
            -item["fvg_created_time_dt"].timestamp(),
        )
    )
    selected = active_candidates[0]
    selected_notes = list(selected["fvg_notes"])
    if selected["fvg_direction"] == structure_bias:
        selected_notes.append("aligned_with_structure_bias")
    if magnet_direction != "neutral" and selected["fvg_direction"] == magnet_direction:
        selected_notes.append("aligned_with_magnet_bias")

    return {
        "fvg_available": True,
        "fvg_direction": selected["fvg_direction"],
        "fvg_state": selected["fvg_state"],
        "fvg_high": selected["fvg_high"],
        "fvg_low": selected["fvg_low"],
        "fvg_mid": selected["fvg_mid"],
        "fvg_size_pips": selected["fvg_size_pips"],
        "fvg_created_time_london": selected["fvg_created_time_london"],
        "fvg_created_time_utc": selected["fvg_created_time_utc"],
        "fvg_age_bars": selected["fvg_age_bars"],
        "fvg_mitigated": selected["fvg_mitigated"],
        "fvg_quality": selected["fvg_quality"],
        "fvg_notes": selected_notes,
    }


def evaluate_setup_readiness(
    *,
    symbol: str,
    anchor_summary: dict,
    sweep_summary: dict,
    magnet_summary: dict,
    zone_state_summary: dict,
    structure_summary: dict,
    fvg_summary: dict,
) -> dict:
    scores = {"bullish": 0, "bearish": 0}
    confirming: dict[str, list[str]] = {"bullish": [], "bearish": []}
    passive_blockers: list[str] = []

    def add_factor(direction: str | None, weight: int, label: str) -> None:
        if direction not in scores or weight <= 0:
            return
        scores[str(direction)] += int(weight)
        confirming[str(direction)].append(label)

    anchor_bias = str(anchor_summary.get("anchor_bias") or "neutral")
    if anchor_bias in scores:
        add_factor(anchor_bias, 10, f"{anchor_bias}_anchor_bias")

    reference_sweep_side = str(sweep_summary.get("reference_sweep_side") or sweep_summary.get("sweep_side") or "")
    sweep_type = str(sweep_summary.get("sweep_type") or "")
    if reference_sweep_side == "sell_side":
        add_factor("bullish", 15 if sweep_type == "rejection_sweep" else 8, f"sellside_{sweep_type or 'sweep'}")
    elif reference_sweep_side == "buy_side":
        add_factor("bearish", 15 if sweep_type == "rejection_sweep" else 8, f"buyside_{sweep_type or 'sweep'}")
    elif str(sweep_summary.get("sweep_side") or "") == "both":
        passive_blockers.append("double_sweep_mixed_context")

    zone_state = str(zone_state_summary.get("zone_state") or "")
    if zone_state == "discount":
        add_factor("bullish", 10, "discount_zone")
    elif zone_state == "premium":
        add_factor("bearish", 10, "premium_zone")

    magnet_bias = str(magnet_summary.get("magnet_bias") or "")
    if magnet_bias == "buyside":
        add_factor("bullish", 10, "buyside_magnet_bias")
    elif magnet_bias == "sellside":
        add_factor("bearish", 10, "sellside_magnet_bias")

    structure_state = str(structure_summary.get("structure_state") or "none")
    structure_bias = str(structure_summary.get("structure_bias") or "neutral")
    if structure_bias in scores and structure_state != "none":
        add_factor(structure_bias, 35 if structure_state.endswith("_mss") else 30, structure_state)

    fvg_direction = str(fvg_summary.get("fvg_direction") or "")
    fvg_state = str(fvg_summary.get("fvg_state") or "none")
    fvg_weight = {
        "fresh": 20,
        "partially_mitigated": 15,
        "fully_mitigated": 5,
    }.get(fvg_state, 0)
    if fvg_direction in scores and fvg_weight > 0:
        add_factor(fvg_direction, fvg_weight, f"{fvg_state}_{fvg_direction}_fvg")

    bullish_score = int(scores["bullish"])
    bearish_score = int(scores["bearish"])
    if bullish_score == 0 and bearish_score == 0:
        return {
            "setup_available": False,
            "setup_direction": None,
            "setup_state": "none",
            "setup_confidence": 0,
            "setup_score": 0,
            "setup_reason": f"No actionable {symbol} setup context is available.",
            "blocking_factors": [],
            "confirming_factors": [],
            "entry_context_summary": (
                f"anchor={anchor_bias}; sweep={sweep_type or 'none'}; magnet={magnet_bias or 'neutral'}; "
                f"zone={zone_state or 'none'}; structure={structure_state}; fvg={fvg_state}"
            ),
        }

    if bullish_score == bearish_score:
        return {
            "setup_available": True,
            "setup_direction": None,
            "setup_state": "conflicted",
            "setup_confidence": bullish_score,
            "setup_score": 0,
            "setup_reason": f"{symbol} context is mixed and lacks a dominant directional bias.",
            "blocking_factors": passive_blockers + [f"conflict:{item}" for item in confirming["bullish"] + confirming["bearish"]],
            "confirming_factors": [],
            "entry_context_summary": (
                f"anchor={anchor_bias}; sweep={sweep_type or 'none'}; magnet={magnet_bias or 'neutral'}; "
                f"zone={zone_state or 'none'}; structure={structure_state}; fvg={fvg_state}"
            ),
        }

    setup_direction = "bullish" if bullish_score > bearish_score else "bearish"
    opposing_direction = "bearish" if setup_direction == "bullish" else "bullish"
    dominant_score = bullish_score if setup_direction == "bullish" else bearish_score
    opposing_score = bearish_score if setup_direction == "bullish" else bullish_score
    signed_score = dominant_score if setup_direction == "bullish" else -dominant_score
    confirming_factors = list(confirming[setup_direction])
    blocking_factors = list(passive_blockers) + [f"conflict:{item}" for item in confirming[opposing_direction]]

    structure_missing = not bool(structure_summary.get("structure_available")) or structure_state == "none"
    aligned_fvg = bool(fvg_summary.get("fvg_available")) and fvg_direction == setup_direction and fvg_state in {
        "fresh",
        "partially_mitigated",
    }

    if structure_missing:
        blocking_factors.append("missing_structure_confirmation")
        setup_state: SetupState = "invalid"
        setup_available = False
        setup_reason = f"{setup_direction.title()} context exists, but structure confirmation is missing."
    elif dominant_score >= 30 and opposing_score >= 30:
        setup_state = "conflicted"
        setup_available = True
        setup_reason = f"{setup_direction.title()} context is present, but opposing evidence is also materially strong."
    elif len(blocking_factors) >= 3 and opposing_score >= 30:
        setup_state = "conflicted"
        setup_available = True
        setup_reason = f"{setup_direction.title()} context is present, but opposing signals keep the setup conflicted."
    elif dominant_score >= 70 and aligned_fvg:
        setup_state = "ready"
        setup_available = True
        setup_reason = f"{setup_direction.title()} {symbol} setup is ready with aligned structure and FVG."
    elif dominant_score >= 45:
        setup_state = "developing"
        setup_available = True
        setup_reason = f"{setup_direction.title()} {symbol} setup is developing but not fully confirmed."
    else:
        setup_state = "none"
        setup_available = False
        setup_reason = f"{symbol} context is too weak to qualify as a setup."

    return {
        "setup_available": setup_available,
        "setup_direction": setup_direction,
        "setup_state": setup_state,
        "setup_confidence": dominant_score,
        "setup_score": signed_score,
        "setup_reason": setup_reason,
        "blocking_factors": blocking_factors,
        "confirming_factors": confirming_factors,
        "entry_context_summary": (
            f"anchor={anchor_bias}; sweep={reference_sweep_side or 'none'}:{sweep_type or 'none'}; "
            f"magnet={magnet_bias or 'neutral'}; zone={zone_state or 'none'}; "
            f"structure={structure_state}; fvg={fvg_direction or 'none'}:{fvg_state}"
        ),
    }


def get_symbol_session_context(
    db: Session,
    *,
    symbol: str,
    as_of_utc: datetime | None = None,
) -> dict:
    symbol_value = (symbol or "").strip().upper()
    config = get_symbol_market_config(symbol_value)
    if config is None:
        raise ValueError(f"Session intelligence is not configured for {symbol_value or 'UNKNOWN'}")

    now_utc = as_utc(as_of_utc or datetime.now(timezone.utc))
    now_london_value = now_utc.astimezone(LONDON_TZ)
    session_state = resolve_session_state(now_london=now_london_value, config=config)
    asian_start_utc, asian_end_utc = _asian_range_bounds_utc(now_london_value=now_london_value, config=config)
    london_start_utc, london_end_utc = _london_session_bounds_utc(now_london_value=now_london_value, config=config)
    previous_day_start_utc, previous_day_end_utc = _previous_day_bounds_utc(now_london_value=now_london_value)
    london_candles_end_utc = min(london_end_utc, now_utc + timedelta(minutes=1))
    anchor_london, anchor_utc = _anchor_target_times(now_london_value=now_london_value, config=config)
    candles = _asian_range_candles(db, symbol=symbol_value, start_utc=asian_start_utc, end_utc=asian_end_utc)
    london_candles = _london_session_candles(
        db,
        symbol=symbol_value,
        start_utc=london_start_utc,
        end_utc=london_candles_end_utc,
    )
    london_m5_candles = _session_timeframe_candles(
        db,
        symbol=symbol_value,
        timeframe="M5",
        start_utc=london_start_utc,
        end_utc=london_candles_end_utc,
    )
    previous_day_candles = _previous_day_candles(
        db,
        symbol=symbol_value,
        start_utc=previous_day_start_utc,
        end_utc=previous_day_end_utc,
    )
    h1_candles = _recent_h1_candles(
        db,
        symbol=symbol_value,
        end_utc=now_utc + timedelta(minutes=1),
        lookback_bars=config.magnet.h1_swing_lookback,
    )
    latest_price_row = _latest_m1_candle(db, symbol=symbol_value, end_utc=now_utc)
    anchor_row = _anchor_candle(db, symbol=symbol_value, anchor_utc=anchor_utc)

    asian_high = max(float(candle.high) for candle in candles) if candles else None
    asian_low = min(float(candle.low) for candle in candles) if candles else None
    asian_mid = ((asian_high + asian_low) / 2.0) if asian_high is not None and asian_low is not None else None
    asian_range_size_pips = calculate_range_size_pips(high=asian_high, low=asian_low, config=config)
    asian_range_valid = bool(
        asian_range_size_pips is not None
        and config.asian_range_min_pips <= asian_range_size_pips <= config.asian_range_max_pips
    )
    anchor_metrics = calculate_candle_metrics(
        open_=float(anchor_row.open) if anchor_row is not None else None,
        high=float(anchor_row.high) if anchor_row is not None else None,
        low=float(anchor_row.low) if anchor_row is not None else None,
        close=float(anchor_row.close) if anchor_row is not None else None,
    )
    anchor_summary = (
        classify_anchor_candle(metrics=anchor_metrics, config=config)
        if anchor_row is not None
        else {
            "anchor_classification": None,
            "anchor_bias": "neutral",
            "anchor_quality": "weak",
            "anchor_notes": ["anchor_candle_not_found"],
        }
    )
    sweep_summary = detect_london_sweep(
        candles=london_candles,
        asian_high=asian_high,
        asian_low=asian_low,
        config=config,
    )
    current_price = float(latest_price_row.close) if latest_price_row is not None else None
    liquidity_summary = _build_liquidity_targets(
        current_price=current_price,
        asian_high=asian_high,
        asian_low=asian_low,
        london_candles=london_candles,
        previous_day_candles=previous_day_candles,
        h1_candles=h1_candles,
        config=config,
    )
    dealing_range_summary = _derive_dealing_range(
        asian_high=asian_high,
        asian_low=asian_low,
        sweep_summary=sweep_summary,
        config=config,
    )
    zone_state_summary = _resolve_zone_state(
        current_price=current_price,
        dealing_range_high=dealing_range_summary["dealing_range_high"],
        dealing_range_low=dealing_range_summary["dealing_range_low"],
        equilibrium=dealing_range_summary["equilibrium"],
        config=config,
    )
    magnet_summary = _resolve_active_magnet(
        current_price=current_price,
        next_buyside=liquidity_summary["next_buyside"],
        next_sellside=liquidity_summary["next_sellside"],
        zone_state=zone_state_summary["zone_state"],
        sweep_summary=sweep_summary,
        anchor_summary=anchor_summary,
        config=config,
    )
    structure_summary = detect_london_structure(
        london_m1_candles=london_candles,
        london_m5_candles=london_m5_candles,
        sweep_summary=sweep_summary,
        zone_state=zone_state_summary["zone_state"],
        config=config,
    )
    fvg_summary = detect_london_fvg(
        london_m1_candles=london_candles,
        structure_summary=structure_summary,
        magnet_bias=magnet_summary["magnet_bias"],
        config=config,
    )
    setup_summary = evaluate_setup_readiness(
        symbol=symbol_value,
        anchor_summary=anchor_summary,
        sweep_summary=sweep_summary,
        magnet_summary=magnet_summary,
        zone_state_summary=zone_state_summary,
        structure_summary=structure_summary,
        fvg_summary=fvg_summary,
    )
    magnet_notes = list(liquidity_summary["notes"]) + list(magnet_summary["magnet_notes"])
    zone_notes = list(dealing_range_summary["zone_notes"]) + list(zone_state_summary["zone_notes"])

    return {
        "symbol": symbol_value,
        "session_state": session_state,
        "asian_high": asian_high,
        "asian_low": asian_low,
        "asian_mid": asian_mid,
        "asian_range_size_pips": asian_range_size_pips,
        "asian_range_valid": asian_range_valid,
        "london_now": now_london_value.isoformat(),
        "source_timeframe_used": "M1",
        "anchor_available": anchor_row is not None,
        "anchor_time_london": anchor_london.isoformat(),
        "anchor_time_utc": anchor_utc.isoformat(),
        "anchor_classification": anchor_summary["anchor_classification"],
        "anchor_bias": anchor_summary["anchor_bias"],
        "anchor_quality": anchor_summary["anchor_quality"],
        "anchor_notes": anchor_summary["anchor_notes"],
        "sweep_available": sweep_summary["sweep_available"],
        "sweep_side": sweep_summary["sweep_side"],
        "sweep_type": sweep_summary["sweep_type"],
        "swept_level": sweep_summary["swept_level"],
        "sweep_buffer_pips": sweep_summary["sweep_buffer_pips"],
        "sweep_time_london": sweep_summary["sweep_time_london"],
        "sweep_time_utc": sweep_summary["sweep_time_utc"],
        "returned_inside_range": sweep_summary["returned_inside_range"],
        "sweep_quality": sweep_summary["sweep_quality"],
        "sweep_notes": sweep_summary["sweep_notes"],
        "magnet_bias": magnet_summary["magnet_bias"],
        "active_magnet_level": magnet_summary["active_magnet_level"],
        "active_magnet_type": magnet_summary["active_magnet_type"],
        "next_buyside_liquidity": liquidity_summary["next_buyside"]["level"]
        if liquidity_summary["next_buyside"] is not None
        else None,
        "next_sellside_liquidity": liquidity_summary["next_sellside"]["level"]
        if liquidity_summary["next_sellside"] is not None
        else None,
        "distance_to_magnet_pips": magnet_summary["distance_to_magnet_pips"],
        "magnet_notes": magnet_notes,
        "zone_state": zone_state_summary["zone_state"],
        "dealing_range_high": dealing_range_summary["dealing_range_high"],
        "dealing_range_low": dealing_range_summary["dealing_range_low"],
        "equilibrium": dealing_range_summary["equilibrium"],
        "distance_from_equilibrium_pips": zone_state_summary["distance_from_equilibrium_pips"],
        "zone_notes": zone_notes,
        "structure_available": structure_summary["structure_available"],
        "structure_state": structure_summary["structure_state"],
        "structure_bias": structure_summary["structure_bias"],
        "mss_detected": structure_summary["mss_detected"],
        "bos_detected": structure_summary["bos_detected"],
        "break_level": structure_summary["break_level"],
        "break_time_london": structure_summary["break_time_london"],
        "break_time_utc": structure_summary["break_time_utc"],
        "displacement_size_pips": structure_summary["displacement_size_pips"],
        "displacement_quality": structure_summary["displacement_quality"],
        "structure_notes": structure_summary["structure_notes"],
        "fvg_available": fvg_summary["fvg_available"],
        "fvg_direction": fvg_summary["fvg_direction"],
        "fvg_state": fvg_summary["fvg_state"],
        "fvg_high": fvg_summary["fvg_high"],
        "fvg_low": fvg_summary["fvg_low"],
        "fvg_mid": fvg_summary["fvg_mid"],
        "fvg_size_pips": fvg_summary["fvg_size_pips"],
        "fvg_created_time_london": fvg_summary["fvg_created_time_london"],
        "fvg_created_time_utc": fvg_summary["fvg_created_time_utc"],
        "fvg_age_bars": fvg_summary["fvg_age_bars"],
        "fvg_mitigated": fvg_summary["fvg_mitigated"],
        "fvg_quality": fvg_summary["fvg_quality"],
        "fvg_notes": fvg_summary["fvg_notes"],
        "setup_available": setup_summary["setup_available"],
        "setup_direction": setup_summary["setup_direction"],
        "setup_state": setup_summary["setup_state"],
        "setup_confidence": setup_summary["setup_confidence"],
        "setup_score": setup_summary["setup_score"],
        "setup_reason": setup_summary["setup_reason"],
        "blocking_factors": setup_summary["blocking_factors"],
        "confirming_factors": setup_summary["confirming_factors"],
        "entry_context_summary": setup_summary["entry_context_summary"],
        "anchor": {
            "anchor_time_london": anchor_london.isoformat(),
            "anchor_time_utc": anchor_utc.isoformat(),
            **anchor_metrics,
        },
        "config": {
            "pip_size": config.pip_size,
            "point_scale": config.point_scale,
            "asian_range_min_pips": config.asian_range_min_pips,
            "asian_range_max_pips": config.asian_range_max_pips,
            "sessions": {
                "asia": {
                    "start": config.asia_session.start.isoformat(),
                    "end": config.asia_session.end.isoformat(),
                },
                "london": {
                    "start": config.london_session.start.isoformat(),
                    "end": config.london_session.end.isoformat(),
                },
                "new_york": {
                    "start": config.new_york_session.start.isoformat(),
                    "end": config.new_york_session.end.isoformat(),
                },
            },
            "atr_placeholder": {
                "h1": {"period": config.atr_h1.period, "enabled": config.atr_h1.enabled},
                "d1": {"period": config.atr_d1.period, "enabled": config.atr_d1.enabled},
            },
            "anchor": {
                "london_open_time": config.anchor.london_open_time.isoformat(),
                "acceptance_body_ratio_min": config.anchor.acceptance_body_ratio_min,
                "rejection_wick_ratio_min": config.anchor.rejection_wick_ratio_min,
                "strong_body_ratio_min": config.anchor.strong_body_ratio_min,
                "strong_wick_ratio_min": config.anchor.strong_wick_ratio_min,
            },
            "sweep": {
                "minimum_buffer_pips": config.sweep.minimum_buffer_pips,
                "lookback_bars": config.sweep.lookback_bars,
                "quality": {
                    "moderate_buffer_pips": config.sweep.quality.moderate_buffer_pips,
                    "strong_buffer_pips": config.sweep.quality.strong_buffer_pips,
                },
            },
            "magnet": {
                "round_number_interval": config.magnet.round_number_interval,
                "h1_swing_lookback": config.magnet.h1_swing_lookback,
                "ranking": {
                    "buyside": list(config.magnet.ranking.buyside),
                    "sellside": list(config.magnet.ranking.sellside),
                },
            },
            "structure": {
                "swing_lookback": config.structure.swing_lookback,
                "break_confirmation_method": config.structure.break_confirmation_method,
                "minimum_displacement_pips": config.structure.minimum_displacement_pips,
                "quality": {
                    "moderate_displacement_pips": config.structure.quality.moderate_displacement_pips,
                    "strong_displacement_pips": config.structure.quality.strong_displacement_pips,
                },
            },
            "fvg": {
                "minimum_gap_size_pips": config.fvg.minimum_gap_size_pips,
                "maximum_age_bars": config.fvg.maximum_age_bars,
                "quality": {
                    "moderate_gap_pips": config.fvg.quality.moderate_gap_pips,
                    "strong_gap_pips": config.fvg.quality.strong_gap_pips,
                },
                "mitigation": {
                    "partial_fill_ratio": config.fvg.mitigation.partial_fill_ratio,
                    "full_fill_ratio": config.fvg.mitigation.full_fill_ratio,
                },
            },
        },
        "asian_range_window": {
            "start_utc": asian_start_utc.isoformat(),
            "end_utc_exclusive": asian_end_utc.isoformat(),
            "candle_count": len(candles),
        },
        "previous_day_window": {
            "start_utc": previous_day_start_utc.isoformat(),
            "end_utc_exclusive": previous_day_end_utc.isoformat(),
            "candle_count": len(previous_day_candles),
        },
        "london_session_window": {
            "start_utc": london_start_utc.isoformat(),
            "end_utc_exclusive": london_end_utc.isoformat(),
            "candle_count": len(london_candles),
            "m5_candle_count": len(london_m5_candles),
        },
    }
