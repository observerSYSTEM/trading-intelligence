from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_DIRECTIONS = {"BUY", "SELL"}
NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class TradeValidationResult:
    ok: bool
    reason: str
    details: dict[str, Any]


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_valid_liquidity_context(liquidity_context: dict[str, Any] | None) -> bool:
    if not isinstance(liquidity_context, dict):
        return False
    candidates = [
        liquidity_context.get("magnet_level"),
        liquidity_context.get("magnet_price"),
        liquidity_context.get("liquidity_target"),
        liquidity_context.get("target"),
        liquidity_context.get("sellside_liquidity"),
        liquidity_context.get("buyside_liquidity"),
        liquidity_context.get("zone_to_zone_target"),
    ]
    for value in candidates:
        price = _safe_float(value)
        if price is not None and price > 0:
            return True
    return False


def validate_trade_payload(
    *,
    direction: str | None,
    entry: Any,
    sl: Any,
    tp: Any,
    daily_permission: str | None = None,
    require_h1_confirmation: bool = False,
    h1_confirm_ok: bool | None = None,
    require_liquidity_context: bool = False,
    liquidity_context: dict[str, Any] | None = None,
) -> TradeValidationResult:
    direction_value = str(direction or "").strip().upper()
    if direction_value not in VALID_DIRECTIONS:
        return TradeValidationResult(
            ok=False,
            reason="invalid direction",
            details={"direction": direction_value},
        )

    entry_value = _safe_float(entry)
    sl_value = _safe_float(sl)
    tp_value = _safe_float(tp)
    if entry_value is None or sl_value is None or tp_value is None:
        return TradeValidationResult(
            ok=False,
            reason="invalid entry/sl/tp structure",
            details={"entry": entry, "sl": sl, "tp": tp},
        )
    if entry_value <= 0 or sl_value <= 0 or tp_value <= 0:
        return TradeValidationResult(
            ok=False,
            reason="invalid entry/sl/tp structure",
            details={"entry": entry_value, "sl": sl_value, "tp": tp_value},
        )

    if direction_value == "BUY":
        if not (tp_value > entry_value and sl_value < entry_value):
            return TradeValidationResult(
                ok=False,
                reason="invalid entry/sl/tp structure",
                details={
                    "direction": direction_value,
                    "entry": entry_value,
                    "sl": sl_value,
                    "tp": tp_value,
                },
            )
    else:
        if not (tp_value < entry_value and sl_value > entry_value):
            return TradeValidationResult(
                ok=False,
                reason="invalid entry/sl/tp structure",
                details={
                    "direction": direction_value,
                    "entry": entry_value,
                    "sl": sl_value,
                    "tp": tp_value,
                },
            )

    permission_value = str(daily_permission or "").strip().upper()
    if permission_value == NO_TRADE:
        return TradeValidationResult(
            ok=False,
            reason="daily permission is NO_TRADE",
            details={"daily_permission": permission_value},
        )

    if require_h1_confirmation and h1_confirm_ok is not True:
        return TradeValidationResult(
            ok=False,
            reason="h1 confirmation required but not true",
            details={"h1_confirm_ok": h1_confirm_ok},
        )

    liquidity_present = _has_valid_liquidity_context(liquidity_context)
    if require_liquidity_context and not liquidity_present:
        return TradeValidationResult(
            ok=False,
            reason="missing liquidity/magnet context",
            details={"liquidity_context_present": liquidity_present},
        )

    return TradeValidationResult(
        ok=True,
        reason="ok",
        details={
            "direction": direction_value,
            "entry": entry_value,
            "sl": sl_value,
            "tp": tp_value,
            "daily_permission": permission_value or None,
            "h1_confirm_ok": h1_confirm_ok,
            "liquidity_context_present": liquidity_present,
        },
    )


def is_valid_trade_setup(**kwargs: Any) -> tuple[bool, str]:
    result = validate_trade_payload(**kwargs)
    return result.ok, result.reason
