from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


EPS = 1e-9
KEY_WINDOWS_UK = {(1, 0): "H4_01", (9, 0): "H4_09", (17, 0): "H4_17"}


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


@dataclass
class H4SessionModifierResult:
    modified_confidence: float
    confidence_delta: float
    reasons_public: list[str]
    reasons_internal: list[str]
    applied: bool
    key_window: str | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _candle_value(candle: Any, key: str, default: Any = None) -> Any:
    if candle is None:
        return default
    if isinstance(candle, dict):
        return candle.get(key, default)
    return getattr(candle, key, default)


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str):
        try:
            return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(0.99, value))


def _in_key_window(last_h4_open_utc: datetime) -> tuple[bool, str | None, datetime]:
    uk_open = _as_utc(last_h4_open_utc).astimezone(UK_TZ)
    window = KEY_WINDOWS_UK.get((uk_open.hour, uk_open.minute))
    return (window is not None), window, uk_open


def _close_position_in_range(*, high: float, low: float, close: float) -> float:
    return (close - low) / max(high - low, EPS)


def _alignment_delta(*, direction: str, open_: float, high: float, low: float, close: float) -> tuple[float, str]:
    direction_value = (direction or "NO_TRADE").strip().upper()
    if direction_value not in {"BUY_ONLY", "SELL_ONLY"}:
        return 0.0, "no_direction_eval"

    close_pos = _close_position_in_range(high=high, low=low, close=close)
    bullish = close > open_
    bearish = close < open_
    upper_half = close_pos >= 0.5
    lower_half = close_pos < 0.5

    if direction_value == "BUY_ONLY":
        if bullish and upper_half:
            return 0.06, "confirm_buy"
        if bearish and lower_half:
            return -0.08, "reject_buy"
        return 0.0, "neutral_buy"

    if bearish and lower_half:
        return 0.06, "confirm_sell"
    if bullish and upper_half:
        return -0.08, "reject_sell"
    return 0.0, "neutral_sell"


def _volume_multiplier(*, last_volume: float | None, prev_volume: float | None) -> tuple[float, float, bool, bool]:
    last_v = float(last_volume or 0.0)
    prev_v = float(prev_volume or 0.0)
    ratio = last_v / max(prev_v, EPS)
    vol_spike = ratio >= 1.3
    vol_drop = ratio <= 0.8
    if vol_spike:
        return 1.25, ratio, True, False
    if vol_drop:
        return 0.85, ratio, False, True
    return 1.0, ratio, False, False


def _liquidity_delta(
    *,
    direction: str,
    last_sweep: dict[str, Any] | None,
    reference_time_utc: datetime | None,
) -> tuple[float, str]:
    if not last_sweep or not isinstance(last_sweep, dict) or reference_time_utc is None:
        return 0.0, "liquidity_missing"

    side = str(last_sweep.get("side") or "").strip().lower()
    sweep_time = _as_datetime(last_sweep.get("time_utc"))
    if side not in {"sellside", "buyside"} or sweep_time is None:
        return 0.0, "liquidity_invalid"

    if (_as_utc(reference_time_utc) - _as_utc(sweep_time)) > timedelta(hours=6):
        return 0.0, "liquidity_stale"

    direction_value = (direction or "").strip().upper()
    if direction_value == "BUY_ONLY":
        if side == "sellside":
            return 0.02, "liquidity_supports"
        if side == "buyside":
            return -0.02, "liquidity_conflicts"
    if direction_value == "SELL_ONLY":
        if side == "buyside":
            return 0.02, "liquidity_supports"
        if side == "sellside":
            return -0.02, "liquidity_conflicts"
    return 0.0, "liquidity_neutral"


def apply_h4_session_flip_modifier(
    *,
    symbol: str,
    allowed_direction: str,
    confidence: float,
    last_h4_candle: Any,
    prev_h4_candle: Any,
    liquidity_last_sweep: dict[str, Any] | None = None,
    pdh_pdl: dict[str, Any] | None = None,
    h4_atr: float | None = None,
) -> H4SessionModifierResult:
    del symbol, pdh_pdl, h4_atr
    current_conf = _clamp_confidence(float(confidence))

    last_open_time = _as_datetime(_candle_value(last_h4_candle, "time_open_utc"))
    if last_open_time is None:
        last_open_time = _as_datetime(_candle_value(last_h4_candle, "time_utc"))
    if last_open_time is None:
        return H4SessionModifierResult(
            modified_confidence=current_conf,
            confidence_delta=0.0,
            reasons_public=[],
            reasons_internal=["h4_missing_timestamp"],
            applied=False,
            key_window=None,
        )

    is_key, key_window, uk_open = _in_key_window(last_open_time)
    if not is_key:
        return H4SessionModifierResult(
            modified_confidence=current_conf,
            confidence_delta=0.0,
            reasons_public=[],
            reasons_internal=[f"h4_non_key_window:{uk_open.strftime('%H:%M')}"],
            applied=False,
            key_window=None,
        )

    open_ = _as_float(_candle_value(last_h4_candle, "open"))
    high = _as_float(_candle_value(last_h4_candle, "high"))
    low = _as_float(_candle_value(last_h4_candle, "low"))
    close = _as_float(_candle_value(last_h4_candle, "close"))
    if open_ is None or high is None or low is None or close is None:
        return H4SessionModifierResult(
            modified_confidence=current_conf,
            confidence_delta=0.0,
            reasons_public=[],
            reasons_internal=["h4_missing_ohlc"],
            applied=False,
            key_window=key_window,
        )

    base_delta, alignment_state = _alignment_delta(
        direction=allowed_direction,
        open_=open_,
        high=high,
        low=low,
        close=close,
    )
    vol_mult, vol_ratio, vol_spike, vol_drop = _volume_multiplier(
        last_volume=_as_float(_candle_value(last_h4_candle, "volume")),
        prev_volume=_as_float(_candle_value(prev_h4_candle, "volume")),
    )
    delta = base_delta * vol_mult

    liquidity_delta, liquidity_state = _liquidity_delta(
        direction=allowed_direction,
        last_sweep=liquidity_last_sweep,
        reference_time_utc=last_open_time + timedelta(hours=4),
    )
    delta += liquidity_delta
    delta = round(delta, 6)

    new_conf = _clamp_confidence(current_conf + delta)
    applied = abs(delta) > 0.0

    reasons_public: list[str] = []
    reasons_internal: list[str] = [
        f"h4_key_window:{key_window}",
        f"alignment_state:{alignment_state}",
        f"volume_ratio:{vol_ratio:.4f}",
        f"volume_multiplier:{vol_mult:.2f}",
        f"liquidity_state:{liquidity_state}",
        f"delta:{delta:+.6f}",
    ]

    if alignment_state in {"confirm_buy", "confirm_sell"}:
        time_label = uk_open.strftime("%H:%M")
        reasons_public.append(f"Key H4 window (UK): {time_label} candle aligned with bias.")
    elif alignment_state in {"reject_buy", "reject_sell"}:
        reasons_public.append("Key H4 window (UK): candle rejected the current bias — caution.")
    elif (allowed_direction or "").strip().upper() == "NO_TRADE":
        time_label = uk_open.strftime("%H:%M")
        reasons_public.append(f"Key H4 window (UK): {time_label} informational check only.")

    if vol_spike:
        reasons_public.append("Volume expanded during key H4 candle — higher conviction.")
    elif vol_drop:
        reasons_public.append("Volume lighter during key H4 candle — lower conviction.")

    if liquidity_state == "liquidity_supports":
        reasons_public.append("Recent liquidity sweep supports the bias.")
    elif liquidity_state == "liquidity_conflicts":
        reasons_public.append("Recent liquidity sweep conflicts with the bias.")

    return H4SessionModifierResult(
        modified_confidence=new_conf,
        confidence_delta=round(new_conf - current_conf, 6),
        reasons_public=reasons_public,
        reasons_internal=reasons_internal,
        applied=applied,
        key_window=key_window,
    )
