from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import MT5Candle, OracleTargetsSnapshot
from app.services.data_provider import get_data_provider
from app.services.loe_engine import evaluate_loe, fallback_loe
from app.services.ppe_engine import evaluate_ppe, fallback_ppe
from app.services.rre_engine import evaluate_rre, fallback_rre
from app.services.tlee_engine import evaluate_tlee, fallback_tlee

logger = logging.getLogger(__name__)


VALID_TIMEFRAMES = {"M1", "M5", "M15", "H1", "H4", "D1"}
MIN_CANDLES = 12


@dataclass(frozen=True)
class LCECandle:
    time_utc: datetime | None
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None
    source: str | None = None


@dataclass(frozen=True)
class LiquidityLevel:
    level: float
    kind: str
    touches: int
    first_index: int
    last_index: int
    source: str
    equal_level: bool
    swept: bool


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _round_price(value: float) -> float:
    magnitude = abs(value)
    if magnitude >= 100:
        return round(value, 2)
    if magnitude >= 10:
        return round(value, 3)
    if magnitude >= 1:
        return round(value, 5)
    return round(value, 6)


def _normalize_timeframe(timeframe: str | None) -> str:
    value = (timeframe or "H1").strip().upper()
    if value not in VALID_TIMEFRAMES:
        raise ValueError(f"Unsupported LCE timeframe '{timeframe}'.")
    return value


def _normalize_candle(raw: Any, *, source: str | None = None) -> LCECandle | None:
    open_value = _as_float(getattr(raw, "open", None))
    high_value = _as_float(getattr(raw, "high", None))
    low_value = _as_float(getattr(raw, "low", None))
    close_value = _as_float(getattr(raw, "close", None))
    if open_value is None or high_value is None or low_value is None or close_value is None:
        return None
    return LCECandle(
        time_utc=_as_utc(getattr(raw, "time_utc", None)),
        open=open_value,
        high=high_value,
        low=low_value,
        close=close_value,
        volume=_as_float(getattr(raw, "volume", None)),
        source=str(getattr(raw, "source", "") or source or "").strip() or None,
    )


def _stored_candles(db: Session, *, symbol: str, timeframe: str, lookback: int) -> list[LCECandle]:
    rows = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == timeframe)
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .limit(max(lookback, MIN_CANDLES))
        .all()
    )
    candles = [_normalize_candle(row, source="stored_candles") for row in reversed(rows)]
    return [candle for candle in candles if candle is not None]


def _provider_candles(symbol: str, timeframe: str, lookback: int) -> list[LCECandle]:
    provider = get_data_provider()
    raw_candles = provider.get_recent_candles(symbol, timeframe, count=max(lookback, MIN_CANDLES))
    source = str(getattr(provider, "name", "") or "provider").strip() or "provider"
    candles = [_normalize_candle(candle, source=source) for candle in raw_candles]
    return [candle for candle in candles if candle is not None]


def _load_candles(db: Session, *, symbol: str, timeframe: str, lookback: int, reasons: list[str]) -> list[LCECandle]:
    stored = _stored_candles(db, symbol=symbol, timeframe=timeframe, lookback=lookback)
    if len(stored) >= MIN_CANDLES:
        reasons.append(f"Loaded {len(stored)} stored {timeframe} candles.")
        return stored[-lookback:]

    if stored:
        reasons.append(f"Stored {timeframe} candles insufficient ({len(stored)}); trying API candle provider.")
    else:
        reasons.append(f"No stored {timeframe} candles found; trying API candle provider.")

    try:
        provider = _provider_candles(symbol, timeframe, lookback)
    except Exception as exc:
        logger.warning("lce provider candle fetch failed symbol=%s timeframe=%s error=%s", symbol, timeframe, exc)
        reasons.append(f"API candle provider unavailable: {exc}")
        return stored[-lookback:]

    if len(provider) >= MIN_CANDLES:
        reasons.append(f"Loaded {len(provider)} {timeframe} candles from API candle provider.")
        return provider[-lookback:]
    reasons.append(f"API candle provider returned insufficient candles ({len(provider)}).")
    return (stored or provider)[-lookback:]


def _average_range(candles: list[LCECandle], length: int = 20) -> float:
    ranges = [max(candle.high - candle.low, 0.0) for candle in candles[-length:] if candle.high >= candle.low]
    return mean(ranges) if ranges else max(abs(candles[-1].close) * 0.001, 0.0001)


def _price_tolerance(candles: list[LCECandle]) -> float:
    current = abs(candles[-1].close)
    return max(_average_range(candles) * 0.12, current * 0.00015, 0.00001)


def _swing_levels(candles: list[LCECandle], *, tolerance: float) -> list[LiquidityLevel]:
    levels: list[LiquidityLevel] = []
    pivot_width = 2
    for idx in range(pivot_width, len(candles) - pivot_width):
        candle = candles[idx]
        left = candles[idx - pivot_width : idx]
        right = candles[idx + 1 : idx + 1 + pivot_width]
        if all(candle.high >= item.high for item in left + right):
            swept = any(item.high > candle.high + tolerance for item in candles[idx + 1 :])
            levels.append(
                LiquidityLevel(
                    level=candle.high,
                    kind="BUYSIDE_LIQUIDITY",
                    touches=1,
                    first_index=idx,
                    last_index=idx,
                    source="swing_high",
                    equal_level=False,
                    swept=swept,
                )
            )
        if all(candle.low <= item.low for item in left + right):
            swept = any(item.low < candle.low - tolerance for item in candles[idx + 1 :])
            levels.append(
                LiquidityLevel(
                    level=candle.low,
                    kind="SELLSIDE_LIQUIDITY",
                    touches=1,
                    first_index=idx,
                    last_index=idx,
                    source="swing_low",
                    equal_level=False,
                    swept=swept,
                )
            )
    return levels


def _group_equal_levels(values: list[tuple[float, int]], *, kind: str, tolerance: float) -> list[LiquidityLevel]:
    groups: list[list[tuple[float, int]]] = []
    for value, idx in sorted(values, key=lambda item: item[0]):
        if not groups or abs(mean(item[0] for item in groups[-1]) - value) > tolerance:
            groups.append([(value, idx)])
        else:
            groups[-1].append((value, idx))

    levels: list[LiquidityLevel] = []
    for group in groups:
        if len(group) < 2:
            continue
        level = mean(item[0] for item in group)
        indexes = [item[1] for item in group]
        levels.append(
            LiquidityLevel(
                level=level,
                kind=kind,
                touches=len(group),
                first_index=min(indexes),
                last_index=max(indexes),
                source="equal_highs" if kind == "BUYSIDE_LIQUIDITY" else "equal_lows",
                equal_level=True,
                swept=False,
            )
        )
    return levels


def _equal_levels(candles: list[LCECandle], *, tolerance: float) -> list[LiquidityLevel]:
    recent = candles[-min(len(candles), 80) :]
    offset = len(candles) - len(recent)
    highs = [(candle.high, offset + idx) for idx, candle in enumerate(recent)]
    lows = [(candle.low, offset + idx) for idx, candle in enumerate(recent)]
    levels = _group_equal_levels(highs, kind="BUYSIDE_LIQUIDITY", tolerance=tolerance)
    levels.extend(_group_equal_levels(lows, kind="SELLSIDE_LIQUIDITY", tolerance=tolerance))

    checked: list[LiquidityLevel] = []
    for level in levels:
        if level.kind == "BUYSIDE_LIQUIDITY":
            swept = any(candle.high > level.level + tolerance for candle in candles[level.last_index + 1 :])
        else:
            swept = any(candle.low < level.level - tolerance for candle in candles[level.last_index + 1 :])
        checked.append(
            LiquidityLevel(
                level=level.level,
                kind=level.kind,
                touches=level.touches,
                first_index=level.first_index,
                last_index=level.last_index,
                source=level.source,
                equal_level=level.equal_level,
                swept=swept,
            )
        )
    return checked


def _dedupe_levels(levels: list[LiquidityLevel], *, tolerance: float) -> list[LiquidityLevel]:
    deduped: list[LiquidityLevel] = []
    for level in sorted(levels, key=lambda item: (item.kind, item.level, -item.touches)):
        match_index = next(
            (
                idx
                for idx, existing in enumerate(deduped)
                if existing.kind == level.kind and abs(existing.level - level.level) <= tolerance
            ),
            None,
        )
        if match_index is None:
            deduped.append(level)
            continue
        existing = deduped[match_index]
        better = level.touches > existing.touches or (level.equal_level and not existing.equal_level)
        if better:
            deduped[match_index] = level
    return deduped


def _latest_targets(db: Session, *, symbol: str) -> OracleTargetsSnapshot | None:
    row = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == "pro")
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


def _target_values(targets: OracleTargetsSnapshot | None) -> list[float]:
    if targets is None:
        return []
    values = [
        targets.magnet_price,
        targets.zone_to_zone_target,
        targets.sellside_liquidity,
        targets.buyside_liquidity,
    ]
    return [value for value in (_as_float(item) for item in values) if value is not None]


def _structure_score(candles: list[LCECandle], checkpoint_type: str) -> tuple[float, str | None]:
    if len(candles) < 20:
        return 0.0, None
    recent_close = candles[-1].close
    prior_close = candles[-min(len(candles), 20)].close
    midpoint = mean(candle.close for candle in candles[-min(len(candles), 12) :])
    bullish = recent_close >= prior_close and recent_close >= midpoint
    bearish = recent_close <= prior_close and recent_close <= midpoint
    if checkpoint_type == "BUYSIDE_LIQUIDITY" and bullish:
        return 0.08, "Current structure leans bullish into buyside liquidity."
    if checkpoint_type == "SELLSIDE_LIQUIDITY" and bearish:
        return 0.08, "Current structure leans bearish into sellside liquidity."
    return 0.0, None


def _score_level(
    level: LiquidityLevel,
    *,
    current_price: float,
    avg_range: float,
    tolerance: float,
    target_values: list[float],
    candles: list[LCECandle],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    distance = abs(level.level - current_price)
    distance_score = max(0.0, 0.24 - min(distance / max(avg_range * 8.0, tolerance), 1.0) * 0.18)
    score = 0.32 + distance_score
    reasons.append(f"Nearest {level.kind.lower().replace('_', ' ')} is {round(distance, 5)} away.")

    if level.touches >= 2:
        touch_bonus = min(0.18, 0.06 * level.touches)
        score += touch_bonus
        reasons.append(f"Level has {level.touches} touches.")
    if level.equal_level:
        score += 0.12
        reasons.append("Level aligns with equal highs/lows.")
    if not level.swept:
        score += 0.14
        reasons.append("Level has not been swept by later candles.")
    if target_values and any(abs(value - level.level) <= max(tolerance * 2.0, avg_range * 0.35) for value in target_values):
        score += 0.12
        reasons.append("Level aligns with existing liquidity magnet/pro targets.")

    structure_bonus, structure_reason = _structure_score(candles, level.kind)
    score += structure_bonus
    if structure_reason:
        reasons.append(structure_reason)

    return min(round(score, 4), 0.95), reasons


def _nearest_candidates(levels: list[LiquidityLevel], *, current_price: float, tolerance: float) -> list[LiquidityLevel]:
    candidates: list[LiquidityLevel] = []
    for level in levels:
        if level.swept:
            continue
        if level.kind == "BUYSIDE_LIQUIDITY" and level.level <= current_price + tolerance:
            continue
        if level.kind == "SELLSIDE_LIQUIDITY" and level.level >= current_price - tolerance:
            continue
        candidates.append(level)
    return sorted(candidates, key=lambda item: abs(item.level - current_price))


def _after_sweep_targets(
    candles: list[LCECandle],
    *,
    checkpoint: float,
    checkpoint_type: str,
    avg_range: float,
    target_values: list[float],
) -> dict[str, list[float]]:
    highs = sorted({candle.high for candle in candles if candle.high > checkpoint})
    lows = sorted({candle.low for candle in candles if candle.low < checkpoint}, reverse=True)
    above_targets = [value for value in target_values if value > checkpoint]
    below_targets = [value for value in target_values if value < checkpoint]

    bullish = sorted(set(highs[:4] + above_targets[:2]))
    bearish = sorted(set(lows[:4] + below_targets[:2]), reverse=True)

    if checkpoint_type == "SELLSIDE_LIQUIDITY":
        bullish = sorted(set([candles[-1].close + avg_range, candles[-1].close + avg_range * 2.0] + bullish))
        bearish = sorted(set([checkpoint - avg_range, checkpoint - avg_range * 2.0] + bearish), reverse=True)
    else:
        bullish = sorted(set([checkpoint + avg_range, checkpoint + avg_range * 2.0] + bullish))
        bearish = sorted(set([candles[-1].close - avg_range, candles[-1].close - avg_range * 2.0] + bearish), reverse=True)

    return {
        "bullish_continuation": [_round_price(value) for value in bullish[:2]],
        "bearish_rejection": [_round_price(value) for value in bearish[:2]],
    }


def _safe_tlee(candles: list[LCECandle]) -> dict[str, Any]:
    try:
        return evaluate_tlee(candles)
    except Exception as exc:
        logger.warning("lce tlee failed error=%s", exc)
        return fallback_tlee(str(exc))


def _safe_loe(candles: list[LCECandle]) -> dict[str, Any]:
    try:
        return evaluate_loe(candles)
    except Exception as exc:
        logger.warning("lce loe failed error=%s", exc)
        return fallback_loe(str(exc))


def _safe_rre(candles: list[LCECandle], *, checkpoint: float | None, checkpoint_type: str | None) -> dict[str, Any]:
    try:
        return evaluate_rre(candles, checkpoint=checkpoint, checkpoint_type=checkpoint_type)
    except Exception as exc:
        logger.warning("lce rre failed error=%s", exc)
        return fallback_rre(str(exc))


def _safe_ppe(candles: list[LCECandle]) -> dict[str, Any]:
    try:
        return evaluate_ppe(candles)
    except Exception as exc:
        logger.warning("lce ppe failed error=%s", exc)
        return fallback_ppe(str(exc))


def _engine_context(
    candles: list[LCECandle],
    *,
    checkpoint: float | None = None,
    checkpoint_type: str | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        "tlee": _safe_tlee(candles),
        "loe": _safe_loe(candles),
        "rre": _safe_rre(candles, checkpoint=checkpoint, checkpoint_type=checkpoint_type),
        "ppe": _safe_ppe(candles),
    }


def _empty_engine_context(error: str | None = None) -> dict[str, dict[str, Any]]:
    return {
        "tlee": fallback_tlee(error),
        "loe": fallback_loe(error),
        "rre": fallback_rre(error),
        "ppe": fallback_ppe(error),
    }


def _probability_score(value: str | None) -> float:
    normalized = str(value or "").strip().upper()
    if normalized == "HIGH":
        return 10.0
    if normalized == "MEDIUM":
        return 5.0
    return 0.0


def _final_bias(
    *,
    checkpoint_type: str | None,
    loe: dict[str, Any],
    ppe: dict[str, Any],
) -> str:
    orderflow = str(loe.get("orderflow_bias") or "").strip().upper()
    zone = str(ppe.get("price_zone") or "").strip().upper()
    if checkpoint_type == "BUYSIDE_LIQUIDITY":
        if orderflow == "SELLERS_BUILDING" or zone == "PREMIUM":
            return "WAIT_FOR_SWEEP_THEN_SELL"
        return "WAIT_FOR_BUYSIDE_SWEEP"
    if checkpoint_type == "SELLSIDE_LIQUIDITY":
        if orderflow == "BUYERS_BUILDING" or zone == "DISCOUNT":
            return "WAIT_FOR_SWEEP_THEN_BUY"
        return "WAIT_FOR_SELLSIDE_SWEEP"
    return "WAIT"


def _stack_confidence(base_confidence: float, context: dict[str, dict[str, Any]]) -> float:
    value = base_confidence * 100.0 if base_confidence <= 1 else base_confidence
    value += _probability_score(context["tlee"].get("expansion_probability"))
    value += max((float(context["loe"].get("confidence") or 0.0) - 50.0) * 0.12, 0.0)
    value += max((float(context["rre"].get("continuation_probability") or 50.0) - 50.0) * 0.10, 0.0)
    if str(context["ppe"].get("price_zone") or "").upper() in {"PREMIUM", "DISCOUNT"}:
        value += 4.0
    return round(min(max(value, 0.0), 100.0), 1)


def build_no_checkpoint_payload(
    *,
    symbol: str,
    timeframe: str,
    reasons: list[str] | None = None,
    context: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    engine_context = context or _empty_engine_context("No checkpoint context available.")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "status": "NO_CHECKPOINT",
        "checkpoint": None,
        "checkpoint_type": None,
        "meaning": "No unswept high-probability liquidity checkpoint was found from the available candles.",
        "after_sweep": {"bullish_continuation": [], "bearish_rejection": []},
        **engine_context,
        "final_bias": "WAIT",
        "confidence": 0.0,
        "reason": reasons or [],
    }


def get_liquidity_checkpoint(
    db: Session,
    *,
    symbol: str = "XAUUSD",
    timeframe: str = "H1",
    lookback: int = 100,
) -> dict[str, Any]:
    symbol_value = (symbol or "XAUUSD").strip().upper()
    timeframe_value = _normalize_timeframe(timeframe)
    lookback_value = max(min(int(lookback or 100), 500), MIN_CANDLES)
    reasons: list[str] = []

    candles = _load_candles(db, symbol=symbol_value, timeframe=timeframe_value, lookback=lookback_value, reasons=reasons)
    if len(candles) < MIN_CANDLES:
        reasons.append(f"Need at least {MIN_CANDLES} candles; found {len(candles)}.")
        return build_no_checkpoint_payload(symbol=symbol_value, timeframe=timeframe_value, reasons=reasons)

    tolerance = _price_tolerance(candles)
    avg_range = _average_range(candles)
    current_price = candles[-1].close
    targets = _latest_targets(db, symbol=symbol_value)
    targets_values = _target_values(targets)

    levels = _dedupe_levels(
        _equal_levels(candles, tolerance=tolerance) + _swing_levels(candles, tolerance=tolerance),
        tolerance=tolerance,
    )
    candidates = _nearest_candidates(levels, current_price=current_price, tolerance=tolerance)
    if not candidates:
        reasons.append("No unswept buyside/sellside levels remain above or below current price.")
        return build_no_checkpoint_payload(
            symbol=symbol_value,
            timeframe=timeframe_value,
            reasons=reasons,
            context=_engine_context(candles),
        )

    nearest_buyside = next((level for level in candidates if level.kind == "BUYSIDE_LIQUIDITY"), None)
    nearest_sellside = next((level for level in candidates if level.kind == "SELLSIDE_LIQUIDITY"), None)
    if nearest_buyside and nearest_sellside:
        chosen = (
            nearest_sellside
            if abs(nearest_sellside.level - current_price) <= abs(nearest_buyside.level - current_price)
            else nearest_buyside
        )
    else:
        chosen = nearest_buyside or nearest_sellside
    if chosen is None:
        reasons.append("No nearest checkpoint could be selected.")
        return build_no_checkpoint_payload(
            symbol=symbol_value,
            timeframe=timeframe_value,
            reasons=reasons,
            context=_engine_context(candles),
        )

    confidence, score_reasons = _score_level(
        chosen,
        current_price=current_price,
        avg_range=avg_range,
        tolerance=tolerance,
        target_values=targets_values,
        candles=candles,
    )
    reasons.extend(score_reasons)
    context = _engine_context(candles, checkpoint=chosen.level, checkpoint_type=chosen.kind)
    final_bias = _final_bias(checkpoint_type=chosen.kind, loe=context["loe"], ppe=context["ppe"])

    return {
        "symbol": symbol_value,
        "timeframe": timeframe_value,
        "status": "WAITING_FOR_SWEEP",
        "checkpoint": _round_price(chosen.level),
        "checkpoint_type": chosen.kind,
        "meaning": "Price is likely to touch/sweep this level first before choosing direction.",
        "after_sweep": _after_sweep_targets(
            candles,
            checkpoint=chosen.level,
            checkpoint_type=chosen.kind,
            avg_range=avg_range,
            target_values=targets_values,
        ),
        **context,
        "final_bias": final_bias,
        "confidence": _stack_confidence(confidence, context),
        "reason": reasons,
    }


def format_lce_telegram_card(result: dict[str, Any]) -> str:
    symbol = result.get("symbol") or "-"
    timeframe = result.get("timeframe") or "-"
    checkpoint = result.get("checkpoint")
    bullish = result.get("after_sweep", {}).get("bullish_continuation", [])
    bearish = result.get("after_sweep", {}).get("bearish_rejection", [])
    status = str(result.get("status") or "NO_CHECKPOINT").replace("_", " ")
    tlee = result.get("tlee") if isinstance(result.get("tlee"), dict) else {}
    loe = result.get("loe") if isinstance(result.get("loe"), dict) else {}
    rre = result.get("rre") if isinstance(result.get("rre"), dict) else {}
    ppe = result.get("ppe") if isinstance(result.get("ppe"), dict) else {}
    final_bias = str(result.get("final_bias") or "WAIT").replace("_", " ")

    def _levels(values: list[Any]) -> str:
        if not values:
            return "-"
        return " -> ".join(str(_round_price(float(value))) for value in values)

    return "\n".join(
        [
            "LCE + TLEE + LOE + RRE + PPE",
            "",
            f"Symbol: {symbol}",
            f"TF: {timeframe}",
            "",
            "Checkpoint:",
            str(checkpoint if checkpoint is not None else "-"),
            "",
            "PPE:",
            f"Premium / Discount: {ppe.get('price_zone') or '-'}",
            "",
            "TLEE:",
            f"Expansion: {tlee.get('expansion_probability') or '-'}",
            "",
            "LOE:",
            str(loe.get("orderflow_bias") or "-").replace("_", " "),
            f"Sell: {loe.get('sell_pressure_percent', '-') }%",
            f"Buy: {loe.get('buy_pressure_percent', '-') }%",
            "",
            "RRE:",
            f"Retracement: {rre.get('retracement_state') or '-'}",
            f"Continuation Probability: {rre.get('continuation_probability', '-')}%",
            "",
            "After Sweep:",
            "",
            "Bullish continuation:",
            _levels(bullish),
            "",
            "Bearish rejection:",
            _levels(bearish),
            "",
            "Final Bias:",
            final_bias,
            "",
            "Status:",
            status,
        ]
    )
