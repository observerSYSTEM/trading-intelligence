from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    try:
        if value is None:
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def _quality(confidence: float, agreement: float, risk: str) -> str:
    if confidence >= 82 and agreement >= 75 and risk == "LOW":
        return "A+"
    if confidence >= 68 and agreement >= 60 and risk in {"LOW", "MEDIUM"}:
        return "A"
    if confidence >= 50:
        return "B"
    return "C"


def _confidence_text(confidence: float) -> str:
    if confidence >= 80:
        return "Very High"
    if confidence >= 65:
        return "High"
    if confidence >= 45:
        return "Medium"
    return "Low"


def _action(decision: str, risk: str, lce_status: str, confidence: float, aligned: bool) -> str:
    if risk == "HIGH" or confidence < 40:
        return "DO_NOT_TRADE"
    if lce_status == "WAITING_FOR_SWEEP":
        return "WAIT_FOR_SWEEP"
    if not aligned:
        return "WAIT_FOR_CONFIRMATION"
    if decision in {"STRONG_BUY", "BUY"}:
        return "BUY_NOW" if confidence >= 70 else "WAIT_FOR_CONFIRMATION"
    if decision in {"STRONG_SELL", "SELL"}:
        return "SELL_NOW" if confidence >= 70 else "WAIT_FOR_CONFIRMATION"
    return "WAIT"


def _levels(values: Any) -> list[float]:
    if not isinstance(values, list):
        return []
    out: list[float] = []
    for value in values:
        parsed = _num(value)
        if parsed is not None:
            out.append(parsed)
    return out


def build_observer_recommendation(*, ode_result: dict[str, Any]) -> dict[str, Any]:
    context = ode_result.get("source_context") if isinstance(ode_result.get("source_context"), dict) else {}
    lce = context.get("lce") if isinstance(context.get("lce"), dict) else {}
    oracle = context.get("oracle_direction") if isinstance(context.get("oracle_direction"), dict) else {}
    after_sweep = lce.get("after_sweep") if isinstance(lce.get("after_sweep"), dict) else {}

    decision = str(ode_result.get("decision") or "NEUTRAL").strip().upper()
    confidence = _num(ode_result.get("confidence")) or 0.0
    agreement = _num(ode_result.get("agreement_score")) or 0.0
    risk = str(ode_result.get("risk_grade") or "HIGH").strip().upper()
    lce_status = str(lce.get("status") or "UNKNOWN").strip().upper()
    aligned = bool(ode_result.get("institutional_alignment"))

    action = _action(decision, risk, lce_status, confidence, aligned)
    checkpoint = _num(lce.get("checkpoint"))
    bullish_targets = _levels(after_sweep.get("bullish_continuation"))
    bearish_targets = _levels(after_sweep.get("bearish_rejection"))
    is_buy = "BUY" in decision
    is_sell = "SELL" in decision

    suggested_entry = checkpoint
    if action in {"BUY_NOW", "SELL_NOW"}:
        suggested_entry = _num(oracle.get("next_buy_liquidity" if is_buy else "next_sell_liquidity")) or checkpoint

    suggested_tp1 = (bullish_targets[0] if is_buy and bullish_targets else bearish_targets[0] if is_sell and bearish_targets else None)
    suggested_tp2 = (bullish_targets[1] if is_buy and len(bullish_targets) > 1 else bearish_targets[1] if is_sell and len(bearish_targets) > 1 else None)
    suggested_sl = None
    if suggested_entry is not None:
        if is_buy:
            suggested_sl = min([value for value in bearish_targets if value < suggested_entry] or [suggested_entry * 0.997])
        elif is_sell:
            suggested_sl = max([value for value in bullish_targets if value > suggested_entry] or [suggested_entry * 1.003])

    expected_path = " -> ".join(
        str(value)
        for value in [
            checkpoint,
            suggested_tp1,
            suggested_tp2,
        ]
        if value is not None
    )

    return {
        "symbol": ode_result.get("symbol"),
        "timeframe": ode_result.get("timeframe"),
        "recommended_action": action,
        "execution_quality": _quality(confidence, agreement, risk),
        "confidence_text": _confidence_text(confidence),
        "suggested_entry": suggested_entry,
        "suggested_sl": suggested_sl,
        "suggested_tp1": suggested_tp1,
        "suggested_tp2": suggested_tp2,
        "expected_liquidity_path": expected_path or "WAIT",
        "institutional_notes": [
            f"ODE decision is {decision.replace('_', ' ')}.",
            f"Agreement score is {agreement:.1f}%.",
            f"Institutional alignment is {bool(aligned)}.",
        ],
        "risk_notes": [
            f"Risk grade is {risk}.",
            "This recommendation is decision support only and does not place trades.",
            "Wait for sweep/confirmation when execution quality is below A.",
        ],
        "ode": ode_result,
    }


def format_observer_recommendation_card(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Observer Recommendation Engine",
            "",
            f"Symbol: {result.get('symbol', '-')}",
            f"TF: {result.get('timeframe', '-')}",
            "",
            f"Action: {str(result.get('recommended_action', '-')).replace('_', ' ')}",
            f"Quality: {result.get('execution_quality', '-')}",
            f"Confidence: {result.get('confidence_text', '-')}",
            "",
            f"Entry: {result.get('suggested_entry', '-')}",
            f"SL: {result.get('suggested_sl', '-')}",
            f"TP1: {result.get('suggested_tp1', '-')}",
            f"TP2: {result.get('suggested_tp2', '-')}",
            "",
            f"Expected Liquidity Path: {result.get('expected_liquidity_path', '-')}",
            "",
            "Institutional Notes:",
            *(str(item) for item in result.get("institutional_notes", [])),
            "",
            "Risk Notes:",
            *(str(item) for item in result.get("risk_notes", [])),
        ]
    )
