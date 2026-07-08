from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.symbols import ALL_SYMBOLS, allowed_symbols_for_tier, configured_symbols_from_settings, normalize_plan
from app.db.models import UserSignalPref, UserSymbolPreference


def _default_enabled_symbols_for_tier(tier: str, available: list[str]) -> list[str]:
    if tier == "basic":
        return ["XAUUSD"] if "XAUUSD" in available else ([available[0]] if available else [])
    if tier == "elite":
        preferred = ["XAUUSD", "GBPJPY"]
        selected = [symbol for symbol in preferred if symbol in available]
        if selected:
            return selected
    if "XAUUSD" in available:
        return ["XAUUSD"]
    return [available[0]] if available else []


def normalize_symbols(values: list[str] | None) -> tuple[list[str], list[str]]:
    selected: list[str] = []
    invalid: list[str] = []
    if not values:
        return selected, invalid

    for raw in values:
        symbol = (raw or "").strip().upper()
        if not symbol:
            continue
        if symbol not in ALL_SYMBOLS:
            if symbol not in invalid:
                invalid.append(symbol)
            continue
        if symbol not in selected:
            selected.append(symbol)
    return selected, invalid


def available_and_locked(plan: str | None) -> tuple[list[str], list[str]]:
    tier = normalize_plan(plan)
    allowed = allowed_symbols_for_tier(tier)
    configured = configured_symbols_from_settings()
    available = [symbol for symbol in configured if symbol in allowed]
    locked = [symbol for symbol in configured if symbol not in allowed]
    return available, locked


def get_user_enabled_symbols(db: Session, user_id, plan: str | None) -> list[str]:
    tier = normalize_plan(plan)
    available, _locked = available_and_locked(tier)

    # Basic always has XAUUSD enabled.
    if tier == "basic":
        return _default_enabled_symbols_for_tier(tier, available)

    signal_pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user_id).first()
    if signal_pref and isinstance(signal_pref.symbols_json, list):
        selected_from_json = [
            str(item).strip().upper()
            for item in signal_pref.symbols_json
            if isinstance(item, str) and str(item).strip().upper() in available
        ]
        if selected_from_json:
            deduped: list[str] = []
            for symbol in selected_from_json:
                if symbol not in deduped:
                    deduped.append(symbol)
            return deduped

    rows = (
        db.query(UserSymbolPreference)
        .filter(UserSymbolPreference.user_id == user_id)
        .all()
    )
    selected = [row.symbol for row in rows if row.enabled and row.symbol in available]
    if selected:
        return selected
    return _default_enabled_symbols_for_tier(tier, available)


def get_user_symbol_preferences_payload(db: Session, user_id, plan: str | None) -> dict:
    tier = normalize_plan(plan)
    available, locked = available_and_locked(tier)
    enabled = set(get_user_enabled_symbols(db, user_id, tier))
    locked_set = set(locked)

    return {
        "tier": tier,
        "available": available,
        "locked": locked,
        "selected": [symbol for symbol in available if symbol in enabled],
        "all": [
            {
                "symbol": symbol,
                "enabled": symbol in enabled,
                "locked": symbol in locked_set,
            }
            for symbol in available + locked
        ],
    }


def upsert_user_symbol_preferences(
    db: Session,
    *,
    user_id,
    plan: str | None,
    selected_symbols: list[str] | None,
) -> dict:
    tier = normalize_plan(plan)
    available, locked = available_and_locked(tier)
    allowed_set = set(available)

    normalized_selected, invalid = normalize_symbols(selected_symbols)
    locked_selected = [symbol for symbol in normalized_selected if symbol not in allowed_set]

    if invalid:
        return {
            "ok": False,
            "error": "invalid_symbols",
            "invalid": invalid,
            "available": available,
            "locked": locked,
        }

    if locked_selected:
        return {
            "ok": False,
            "error": "locked_symbols",
            "locked_selected": locked_selected,
            "available": available,
            "locked": locked,
        }

    selected_set = set(normalized_selected)
    if tier == "basic":
        selected_set = {"XAUUSD"}

    now = datetime.now(timezone.utc)
    rows = (
        db.query(UserSymbolPreference)
        .filter(UserSymbolPreference.user_id == user_id)
        .filter(UserSymbolPreference.symbol.in_(available))
        .all()
    )
    by_symbol = {row.symbol: row for row in rows}

    for symbol in available:
        enabled = symbol in selected_set
        row = by_symbol.get(symbol)
        if row:
            row.enabled = enabled
            row.updated_at = now
        else:
            db.add(
                UserSymbolPreference(
                    user_id=user_id,
                    symbol=symbol,
                    enabled=enabled,
                    updated_at=now,
                )
            )

    selected = [symbol for symbol in available if symbol in selected_set]
    signal_pref = db.query(UserSignalPref).filter(UserSignalPref.user_id == user_id).first()
    if not signal_pref:
        signal_pref = UserSignalPref(user_id=user_id)
        db.add(signal_pref)
    signal_pref.symbols_json = selected
    signal_pref.updated_at = now

    return {
        "ok": True,
        "tier": tier,
        "selected": selected,
        "available": available,
        "locked": locked,
    }
