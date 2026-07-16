from __future__ import annotations

from typing import Any, Callable


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _direction_vote(value: Any) -> str:
    normalized = str(value or "").strip().upper().replace(" ", "_")
    if normalized in {"STRONG_BUY", "BUY", "BUY_ONLY", "BULLISH"}:
        return "BUY"
    if normalized in {"STRONG_SELL", "SELL", "SELL_ONLY", "BEARISH"}:
        return "SELL"
    return "NEUTRAL"


def _lce_vote(lce: dict[str, Any]) -> str:
    bias = str(lce.get("final_bias") or "").strip().upper()
    if "BUY" in bias:
        return "BUY"
    if "SELL" in bias:
        return "SELL"
    checkpoint_type = str(lce.get("checkpoint_type") or "").strip().upper()
    if checkpoint_type == "SELLSIDE_LIQUIDITY":
        return "BUY"
    if checkpoint_type == "BUYSIDE_LIQUIDITY":
        return "SELL"
    return "NEUTRAL"


def _tlee_vote(lce: dict[str, Any]) -> str:
    tlee = lce.get("tlee") if isinstance(lce.get("tlee"), dict) else {}
    probability = str(tlee.get("expansion_probability") or "").strip().upper()
    if probability not in {"MEDIUM", "HIGH"}:
        return "PASS"
    return _lce_vote(lce)


def _loe_vote(lce: dict[str, Any]) -> str:
    loe = lce.get("loe") if isinstance(lce.get("loe"), dict) else {}
    bias = str(loe.get("orderflow_bias") or "").strip().upper()
    if bias == "BUYERS_BUILDING":
        return "BUY"
    if bias == "SELLERS_BUILDING":
        return "SELL"
    return "NEUTRAL"


def _rre_vote(lce: dict[str, Any]) -> str:
    rre = lce.get("rre") if isinstance(lce.get("rre"), dict) else {}
    continuation = _as_float(rre.get("continuation_probability"), 50.0)
    reversal = _as_float(rre.get("reversal_risk"), 50.0)
    if continuation >= 60.0 and continuation >= reversal:
        return "PASS"
    if reversal >= 65.0:
        lce_direction = _lce_vote(lce)
        if lce_direction == "BUY":
            return "SELL"
        if lce_direction == "SELL":
            return "BUY"
    return "NEUTRAL"


def _ppe_vote(lce: dict[str, Any]) -> str:
    ppe = lce.get("ppe") if isinstance(lce.get("ppe"), dict) else {}
    zone = str(ppe.get("price_zone") or "").strip().upper()
    if zone == "DISCOUNT":
        return "BUY"
    if zone == "PREMIUM":
        return "SELL"
    return "NEUTRAL"


def _ppe_grade(lce: dict[str, Any]) -> str:
    ppe = lce.get("ppe") if isinstance(lce.get("ppe"), dict) else {}
    zone = str(ppe.get("price_zone") or "").strip().upper()
    if zone in {"PREMIUM", "DISCOUNT"}:
        return "HIGH"
    if zone == "EQUILIBRIUM":
        return "MEDIUM"
    return "LOW"


def _daily_bias_vote(value: Any) -> str:
    return _direction_vote(value)


def extract_daily_bias_from_snapshot(snapshot: Any, *, plan: str) -> str | None:
    if snapshot is None:
        return None
    public = snapshot.public_factors_json if isinstance(getattr(snapshot, "public_factors_json", None), dict) else {}
    plan_value = str(plan or "basic").strip().lower()
    if plan_value == "elite":
        value = getattr(snapshot, "final_allowed_elite", None) or public.get("final_allowed_elite")
    else:
        value = getattr(snapshot, "final_allowed_basic", None) or public.get("final_allowed_basic")
    value = (
        value
        or getattr(snapshot, "daily_bias", None)
        or public.get("daily_bias_raw")
        or public.get("daily_bias")
        or getattr(snapshot, "allowed_direction", None)
    )
    return str(value).strip().upper() if value else None


def _vote_score(vote: str) -> int:
    if vote == "BUY":
        return 1
    if vote == "SELL":
        return -1
    return 0


def _decision_from_score(score: float, confidence: float) -> str:
    if score >= 0.66 and confidence >= 72:
        return "STRONG_BUY"
    if score >= 0.22:
        return "BUY"
    if score <= -0.66 and confidence >= 72:
        return "STRONG_SELL"
    if score <= -0.22:
        return "SELL"
    return "NEUTRAL"


def _risk_grade(confidence: float, agreement: float, lce: dict[str, Any]) -> str:
    status = str(lce.get("status") or "").strip().upper()
    if status in {"ERROR", "NO_CHECKPOINT"}:
        return "HIGH"
    if confidence >= 75 and agreement >= 70:
        return "LOW"
    if confidence >= 55 and agreement >= 50:
        return "MEDIUM"
    return "HIGH"


def _confidence_text(value: float) -> str:
    if value >= 80:
        return "Very High"
    if value >= 65:
        return "High"
    if value >= 45:
        return "Medium"
    return "Low"


def build_observer_decision(
    *,
    symbol: str,
    timeframe: str,
    lce_result: dict[str, Any],
    oracle_direction: dict[str, Any],
    daily_bias: str | None,
    reason_builder: Callable[[dict[str, str], str], list[str]] | None = None,
) -> dict[str, Any]:
    oracle_vote = _direction_vote(oracle_direction.get("direction"))
    lce_vote = _lce_vote(lce_result)
    ppe_direction = _ppe_vote(lce_result)
    votes = {
        "oracle": oracle_vote,
        "lce": lce_vote,
        "tlee": _tlee_vote(lce_result),
        "loe": _loe_vote(lce_result),
        "rre": _rre_vote(lce_result),
        "ppe": _ppe_grade(lce_result),
        "daily_bias": _daily_bias_vote(daily_bias),
    }
    scoring_votes = {**votes, "ppe": ppe_direction}
    directional_votes = [vote for vote in scoring_votes.values() if vote in {"BUY", "SELL"}]
    vote_total = sum(_vote_score(vote) for vote in scoring_votes.values())
    active_count = max(len([vote for vote in scoring_votes.values() if vote != "PASS"]), 1)
    normalized_score = vote_total / active_count
    agreement = 0.0
    if directional_votes:
        majority = "BUY" if sum(1 for vote in directional_votes if vote == "BUY") >= sum(1 for vote in directional_votes if vote == "SELL") else "SELL"
        agreement = round((sum(1 for vote in directional_votes if vote == majority) / len(directional_votes)) * 100.0, 1)

    oracle_confidence = _as_float(oracle_direction.get("confidence_percent"), 50.0)
    lce_confidence = _as_float(lce_result.get("confidence"), 50.0)
    confidence = round(min(max((oracle_confidence * 0.45) + (lce_confidence * 0.35) + (agreement * 0.20), 0.0), 100.0), 1)
    decision = _decision_from_score(normalized_score, confidence)
    risk = _risk_grade(confidence, agreement, lce_result)
    institutional_alignment = agreement >= 70.0 and risk != "HIGH" and decision != "NEUTRAL"

    reasoning = reason_builder(votes, decision) if reason_builder else []
    if not reasoning:
        reasoning = [
            f"Oracle vote is {oracle_vote}.",
            f"LCE vote is {lce_vote} with status {lce_result.get('status', 'UNKNOWN')}.",
            f"Agreement score is {agreement:.1f}%.",
            f"Risk grade is {risk}.",
        ]
        if daily_bias:
            reasoning.append(f"Daily Bias vote is {votes['daily_bias']}.")

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "decision": decision,
        "confidence": confidence,
        "confidence_text": _confidence_text(confidence),
        "agreement_score": agreement,
        "engine_votes": votes,
        "reasoning": reasoning,
        "risk_grade": risk,
        "institutional_alignment": bool(institutional_alignment),
        "source_context": {
            "lce": {
                "checkpoint": lce_result.get("checkpoint"),
                "checkpoint_type": lce_result.get("checkpoint_type"),
                "status": lce_result.get("status"),
                "after_sweep": lce_result.get("after_sweep", {}),
                "final_bias": lce_result.get("final_bias"),
                "confidence": lce_result.get("confidence"),
            },
            "oracle_direction": {
                "direction": oracle_direction.get("direction"),
                "buy_percent": oracle_direction.get("buy_percent"),
                "sell_percent": oracle_direction.get("sell_percent"),
                "confidence_percent": oracle_direction.get("confidence_percent"),
                "next_buy_liquidity": oracle_direction.get("next_buy_liquidity"),
                "next_sell_liquidity": oracle_direction.get("next_sell_liquidity"),
            },
            "daily_bias": daily_bias,
        },
    }


def format_observer_decision_card(result: dict[str, Any]) -> str:
    return "\n".join(
        [
            "Observer Decision Engine",
            "",
            f"Symbol: {result.get('symbol', '-')}",
            f"TF: {result.get('timeframe', '-')}",
            "",
            f"Decision: {str(result.get('decision', '-')).replace('_', ' ')}",
            f"Confidence: {result.get('confidence', '-')}%",
            f"Agreement: {result.get('agreement_score', '-')}%",
            f"Risk: {result.get('risk_grade', '-')}",
            f"Institutional Alignment: {bool(result.get('institutional_alignment'))}",
            "",
            "Votes:",
            *(f"{key}: {value}" for key, value in (result.get("engine_votes") or {}).items()),
        ]
    )
