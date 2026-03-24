from __future__ import annotations

from datetime import timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.oracle_engine import compute_hourly_candidate, confirm_with_m15


def _safe_targets(public_json: dict[str, Any]) -> dict[str, float | None]:
    targets_json = public_json.get("targets_json") if isinstance(public_json.get("targets_json"), dict) else {}
    return {
        "target": _as_float(targets_json.get("target")),
        "reaction": _as_float(targets_json.get("reaction")),
        "liquidity_high": _as_float(targets_json.get("liquidity_high")),
        "liquidity_low": _as_float(targets_json.get("liquidity_low")),
    }


def _as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def run_oracle_for_symbol(db: Session, symbol: str) -> dict[str, Any]:
    symbol_value = symbol.strip().upper()
    candidate = compute_hourly_candidate(db, symbol=symbol_value)
    confirm = confirm_with_m15(
        db,
        symbol=symbol_value,
        candidate_bias=candidate.bias,
        candidate_as_of_utc=candidate.as_of_utc,
    )

    public_json = candidate.public_json if isinstance(candidate.public_json, dict) else {}
    final_allowed = candidate.bias if confirm.confirm_ok else "NO_TRADE"
    confirm_status = "CONFIRMED" if confirm.confirm_ok else "NOT_CONFIRMED"
    targets = _safe_targets(public_json)

    return {
        "symbol": symbol_value,
        "allowed_direction": final_allowed,
        "confidence": candidate.confidence,
        "trigger_timeframe": candidate.timeframe,
        # Public contract uses H1 as the directional confirmation frame.
        "confirmation_timeframe": "H1",
        "confirm_status": confirm_status,
        "as_of_utc": candidate.as_of_utc.astimezone(timezone.utc).isoformat(),
        "liquidity_targets": {
            "target": targets["target"],
            "reaction": targets["reaction"],
            "liquidity_high": targets["liquidity_high"],
            "liquidity_low": targets["liquidity_low"],
        },
        "elite_extras": {
            "risk_banner": public_json.get("risk_banner", {}),
            "vol_state": public_json.get("vol_state"),
            "atr_h1": public_json.get("atr_h1"),
            "confirm_reason": confirm.reason_json,
            "manipulation_level": confirm.manipulation_level,
        },
    }
