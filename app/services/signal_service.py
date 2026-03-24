from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import LiquiditySignal, RunnerStatus
from app.schemas.signal import SignalCreate

_REFRESH_LOOKBACK = 50


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _bucket_datetime(value: datetime, bucket_seconds: int) -> datetime:
    seconds = max(int(bucket_seconds), 1)
    ts = int(_as_utc(value).timestamp())
    bucketed = (ts // seconds) * seconds
    return datetime.fromtimestamp(bucketed, tz=timezone.utc)


def _float_token(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.5f}"


def _safe_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        clean = value.strip()
        if not clean or clean == "-":
            return None
        value = clean
    try:
        parsed = float(value)
    except Exception:
        return None
    if parsed != parsed:
        return None
    return parsed


def _clean_text(value) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    if not clean or clean == "-":
        return None
    return clean


def _normalize_confirmation(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "CONFIRMED" if value else "NOT_CONFIRMED"
    clean = _clean_text(value)
    if clean is None:
        return None
    upper = clean.upper()
    if upper in {"TRUE", "YES", "OK", "CONFIRMED"}:
        return "CONFIRMED"
    if upper in {"FALSE", "NO", "REJECTED", "NOT_CONFIRMED"}:
        return "NOT_CONFIRMED"
    return upper


def _meta_float(meta: dict, *keys: str) -> float | None:
    for key in keys:
        parsed = _safe_float(meta.get(key))
        if parsed is not None:
            return parsed
    return None


def _meta_text(meta: dict, *keys: str) -> str | None:
    for key in keys:
        clean = _clean_text(meta.get(key))
        if clean is not None:
            return clean
    return None


def _meta_dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def payload_signal_details(payload: SignalCreate) -> dict:
    meta = _meta_dict(payload.meta)
    magnet = (
        _safe_float(payload.magnet)
        if payload.magnet is not None
        else _safe_float(payload.magnet_level)
    )
    if magnet is None:
        magnet = _meta_float(meta, "magnet", "magnet_level", "magnet_price")

    zone_target = _safe_float(payload.zone_target)
    if zone_target is None:
        zone_target = _meta_float(meta, "zone_target", "zone_to_zone_target")

    sellside_liquidity = _safe_float(payload.sellside_liquidity)
    if sellside_liquidity is None:
        sellside_liquidity = _meta_float(meta, "sellside_liquidity")

    buyside_liquidity = _safe_float(payload.buyside_liquidity)
    if buyside_liquidity is None:
        buyside_liquidity = _meta_float(meta, "buyside_liquidity")

    confidence = _safe_float(payload.confidence)
    if confidence is None:
        confidence = _meta_float(meta, "confidence")

    reason = _clean_text(payload.reason)
    if reason is None:
        reason = _meta_text(meta, "reason", "reason_short")

    daily_permission = _clean_text(payload.daily_permission)
    if daily_permission is None:
        daily_permission = _meta_text(meta, "daily_permission")
    if daily_permission is None:
        daily_permission = _clean_text(payload.bias)

    h1_confirmation = _normalize_confirmation(payload.h1_confirmation)
    if h1_confirmation is None:
        h1_confirmation = _normalize_confirmation(meta.get("h1_confirmation"))
    if h1_confirmation is None:
        h1_confirmation = _normalize_confirmation(meta.get("h1_confirm_ok"))

    return {
        "magnet": magnet,
        "zone_target": zone_target,
        "sellside_liquidity": sellside_liquidity,
        "buyside_liquidity": buyside_liquidity,
        "confidence": confidence,
        "reason": reason,
        "daily_permission": daily_permission,
        "h1_confirmation": h1_confirmation,
        "active_setup_key": _meta_text(meta, "active_setup_key"),
    }


def extract_signal_fields(row: LiquiditySignal) -> dict:
    meta = _meta_dict(row.meta_json)
    magnet = _meta_float(meta, "magnet")
    if magnet is None:
        magnet = _safe_float(row.magnet_level)
    if magnet is None:
        magnet = _meta_float(meta, "magnet_level", "magnet_price")

    zone_target = _meta_float(meta, "zone_target", "zone_to_zone_target")
    sellside_liquidity = _meta_float(meta, "sellside_liquidity")
    buyside_liquidity = _meta_float(meta, "buyside_liquidity")
    confidence = _meta_float(meta, "confidence")
    reason = _meta_text(meta, "reason", "reason_short")
    daily_permission = _meta_text(meta, "daily_permission")
    if daily_permission is None:
        daily_permission = _clean_text(row.bias)
    h1_confirmation = _normalize_confirmation(meta.get("h1_confirmation"))
    if h1_confirmation is None:
        h1_confirmation = _normalize_confirmation(meta.get("h1_confirm_ok"))

    return {
        "type": row.signal_type,
        "magnet": magnet,
        "zone_target": zone_target,
        "sellside_liquidity": sellside_liquidity,
        "buyside_liquidity": buyside_liquidity,
        "confidence": confidence,
        "reason": reason,
        "daily_permission": daily_permission,
        "h1_confirmation": h1_confirmation,
        "active_setup_key": _meta_text(meta, "active_setup_key"),
    }


def _normalized_compare_value(value):
    if isinstance(value, float):
        return round(value, 5)
    if isinstance(value, str):
        return value.strip().upper()
    return value


def find_refreshable_signal(db: Session, *, payload: SignalCreate) -> LiquiditySignal | None:
    details = payload_signal_details(payload)
    active_setup_key = details.get("active_setup_key")
    if not active_setup_key:
        return None

    candidates = (
        db.query(LiquiditySignal)
        .filter(
            LiquiditySignal.symbol == payload.symbol.strip().upper(),
            LiquiditySignal.timeframe == payload.timeframe.strip().upper(),
            LiquiditySignal.signal_type == payload.signal_type.strip().lower(),
            LiquiditySignal.source == payload.source.strip().lower(),
        )
        .order_by(LiquiditySignal.detected_at.desc(), LiquiditySignal.created_at.desc())
        .limit(_REFRESH_LOOKBACK)
        .all()
    )
    for row in candidates:
        current = extract_signal_fields(row)
        if current.get("active_setup_key") == active_setup_key:
            return row
    return None


def signal_payload_requires_refresh(row: LiquiditySignal, *, payload: SignalCreate) -> bool:
    incoming = payload_signal_details(payload)
    current = extract_signal_fields(row)

    if _as_utc(payload.detected_at) > _as_utc(row.detected_at):
        return True
    if _clean_text(payload.direction) != _clean_text(row.direction):
        return True
    if _clean_text(payload.bias) != _clean_text(row.bias):
        return True

    incoming_price = _safe_float(payload.price)
    current_price = _safe_float(row.price)
    if incoming_price is not None and _normalized_compare_value(incoming_price) != _normalized_compare_value(current_price):
        return True

    for key in (
        "magnet",
        "zone_target",
        "sellside_liquidity",
        "buyside_liquidity",
        "confidence",
        "reason",
        "daily_permission",
        "h1_confirmation",
    ):
        incoming_value = incoming.get(key)
        if incoming_value is None:
            continue
        if _normalized_compare_value(incoming_value) != _normalized_compare_value(current.get(key)):
            return True
    return False


def _merge_signal_meta(existing_meta: dict, payload: SignalCreate, *, current_fields: dict) -> dict:
    merged = dict(existing_meta)
    merged.update(_meta_dict(payload.meta))
    incoming = payload_signal_details(payload)

    magnet = incoming.get("magnet")
    if magnet is None:
        magnet = current_fields.get("magnet")
    if magnet is not None:
        merged["magnet"] = magnet

    zone_target = incoming.get("zone_target")
    if zone_target is None:
        zone_target = current_fields.get("zone_target")
    if zone_target is not None:
        merged["zone_target"] = zone_target
        if _meta_float(merged, "zone_to_zone_target") is None:
            merged["zone_to_zone_target"] = zone_target

    for key in ("sellside_liquidity", "buyside_liquidity", "confidence"):
        value = incoming.get(key)
        if value is None:
            value = current_fields.get(key)
        if value is not None:
            merged[key] = value

    for key in ("reason", "daily_permission", "active_setup_key"):
        value = incoming.get(key)
        if value is None:
            value = current_fields.get(key)
        if value is not None:
            merged[key] = value

    h1_confirmation = incoming.get("h1_confirmation")
    if h1_confirmation is None:
        h1_confirmation = current_fields.get("h1_confirmation")
    if h1_confirmation is not None:
        merged["h1_confirmation"] = h1_confirmation
        merged["h1_confirm_ok"] = h1_confirmation == "CONFIRMED"

    merged["type"] = payload.signal_type.strip().lower()
    return merged


def _touch_runner_status(db: Session, *, detected_at: datetime) -> None:
    status = db.query(RunnerStatus).order_by(RunnerStatus.updated_at.desc()).first()
    if status is not None:
        status.last_signal_utc = _as_utc(detected_at)
        db.add(status)


def _apply_payload_to_row(row: LiquiditySignal, *, payload: SignalCreate, dedup_key: str) -> bool:
    current = extract_signal_fields(row)
    current["daily_permission"] = current.get("daily_permission") or _clean_text(row.bias)
    before = (
        row.direction,
        _safe_float(row.magnet_level),
        _safe_float(row.price),
        row.bias,
        _as_utc(row.detected_at),
        row.dedup_key,
        row.meta_json,
    )

    merged_meta = _merge_signal_meta(_meta_dict(row.meta_json), payload, current_fields=current)
    incoming = payload_signal_details(payload)
    magnet = incoming.get("magnet")
    if magnet is None:
        magnet = current.get("magnet")

    row.symbol = payload.symbol.strip().upper()
    row.timeframe = payload.timeframe.strip().upper()
    row.signal_type = payload.signal_type.strip().lower()
    row.direction = _clean_text(payload.direction)
    row.magnet_level = magnet
    row.price = _safe_float(payload.price)
    row.bias = (_clean_text(payload.bias) or _clean_text(incoming.get("daily_permission")))
    row.source = payload.source.strip().lower()
    row.detected_at = max(_as_utc(row.detected_at), _as_utc(payload.detected_at))
    row.meta_json = merged_meta
    row.dedup_key = dedup_key

    after = (
        row.direction,
        _safe_float(row.magnet_level),
        _safe_float(row.price),
        row.bias,
        _as_utc(row.detected_at),
        row.dedup_key,
        row.meta_json,
    )
    return before != after


def build_dedup_key(payload: SignalCreate, bucket_seconds: int = 60) -> str:
    bucket_dt = _bucket_datetime(payload.detected_at, bucket_seconds)
    details = payload_signal_details(payload)
    reason = _clean_text(details.get("reason"))
    base = "|".join(
        [
            payload.symbol.strip().upper(),
            payload.timeframe.strip().upper(),
            payload.signal_type.strip().lower(),
            (payload.direction or "").strip().upper(),
            (payload.bias or "").strip().upper(),
            _float_token(_safe_float(details.get("magnet"))),
            _float_token(_safe_float(details.get("zone_target"))),
            _float_token(_safe_float(details.get("confidence"))),
            (str(details.get("daily_permission") or "").strip().upper()),
            (str(details.get("h1_confirmation") or "").strip().upper()),
            (reason or "")[:120].lower(),
            payload.source.strip().lower(),
            bucket_dt.isoformat(),
        ]
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def check_duplicate(db: Session, *, dedup_key: str) -> LiquiditySignal | None:
    clean = dedup_key.strip()
    if not clean:
        return None
    return db.query(LiquiditySignal).filter(LiquiditySignal.dedup_key == clean).first()


def create_signal(db: Session, *, payload: SignalCreate, bucket_seconds: int = 60) -> tuple[LiquiditySignal, bool]:
    dedup_key = (payload.dedup_key or "").strip() or build_dedup_key(payload, bucket_seconds=bucket_seconds)
    existing = check_duplicate(db, dedup_key=dedup_key)
    if existing:
        updated = _apply_payload_to_row(existing, payload=payload, dedup_key=dedup_key)
        db.add(existing)
        db.flush()
        _touch_runner_status(db, detected_at=existing.detected_at)
        db.flush()
        return existing, not updated

    refreshable = find_refreshable_signal(db, payload=payload)
    if refreshable is not None:
        updated = _apply_payload_to_row(refreshable, payload=payload, dedup_key=dedup_key)
        db.add(refreshable)
        db.flush()
        _touch_runner_status(db, detected_at=refreshable.detected_at)
        db.flush()
        return refreshable, not updated

    details = payload_signal_details(payload)
    meta_json = _merge_signal_meta({}, payload, current_fields={})

    row = LiquiditySignal(
        symbol=payload.symbol.strip().upper(),
        timeframe=payload.timeframe.strip().upper(),
        signal_type=payload.signal_type.strip().lower(),
        direction=(payload.direction or "").strip().upper() or None,
        magnet_level=_safe_float(details.get("magnet")),
        price=payload.price,
        bias=((payload.bias or payload.daily_permission or "").strip().upper() or None),
        source=payload.source.strip().lower(),
        detected_at=_as_utc(payload.detected_at),
        meta_json=meta_json,
        dedup_key=dedup_key,
    )
    db.add(row)
    try:
        db.flush()
        _touch_runner_status(db, detected_at=payload.detected_at)
        db.flush()
        return row, False
    except IntegrityError:
        db.rollback()
        duplicate = check_duplicate(db, dedup_key=dedup_key)
        if duplicate:
            return duplicate, True
        raise


def get_signals(
    db: Session,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    signal_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[LiquiditySignal], int]:
    query = db.query(LiquiditySignal)
    if symbol:
        query = query.filter(LiquiditySignal.symbol == symbol.strip().upper())
    if timeframe:
        query = query.filter(LiquiditySignal.timeframe == timeframe.strip().upper())
    if signal_type:
        query = query.filter(LiquiditySignal.signal_type == signal_type.strip().lower())

    total = query.count()
    items = (
        query.order_by(LiquiditySignal.detected_at.desc(), LiquiditySignal.created_at.desc())
        .offset(max(int(offset), 0))
        .limit(max(min(int(limit), 200), 1))
        .all()
    )
    return items, total


def get_latest_signals(
    db: Session,
    *,
    symbol: str | None = None,
    timeframe: str | None = None,
    signal_type: str | None = None,
    limit: int = 20,
) -> Sequence[LiquiditySignal]:
    items, _total = get_signals(
        db,
        symbol=symbol,
        timeframe=timeframe,
        signal_type=signal_type,
        limit=limit,
        offset=0,
    )
    return items


def get_signal_by_id(db: Session, *, signal_id: UUID) -> LiquiditySignal | None:
    return db.query(LiquiditySignal).filter(LiquiditySignal.id == signal_id).first()
