from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.rate_limit import RateLimitRule, rate_limit
from app.core.symbols import allowed_symbols_for_plan, normalize_plan
from app.db.models import GoldRegimeDaily, LiquiditySignal, MT5Candle, OracleTargetsSnapshot, Subscription, User
from app.db.session import get_db
from app.schemas.signal import SignalCreate, SignalCreateResult, SignalListOut, SignalOut
from app.services.signal_service import create_signal, extract_signal_fields, get_latest_signals, get_signal_by_id, get_signals
from app.services.targets_refresh import recompute_targets_snapshot
from app.services.symbol_preferences import get_user_enabled_symbols

router = APIRouter(prefix="/signals", tags=["signals"])
logger = logging.getLogger(__name__)

TIER_ORDER = {"basic": 0, "pro": 1, "elite": 2}


def _serialize_signal(row: LiquiditySignal) -> SignalOut:
    detected_at = row.detected_at
    if detected_at.tzinfo is None:
        detected_at = detected_at.replace(tzinfo=timezone.utc)
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    details = extract_signal_fields(row)
    return SignalOut(
        id=str(row.id),
        symbol=row.symbol,
        timeframe=row.timeframe,
        type=row.signal_type,
        signal_type=row.signal_type,
        direction=row.direction,
        magnet=details.get("magnet"),
        magnet_level=row.magnet_level,
        price=row.price,
        bias=row.bias,
        reason=details.get("reason"),
        confidence=details.get("confidence"),
        daily_permission=details.get("daily_permission"),
        h1_confirmation=details.get("h1_confirmation"),
        zone_target=details.get("zone_target"),
        sellside_liquidity=details.get("sellside_liquidity"),
        buyside_liquidity=details.get("buyside_liquidity"),
        source=row.source,
        detected_at=detected_at,
        meta=row.meta_json if isinstance(row.meta_json, dict) else {},
        dedup_key=row.dedup_key,
        created_at=created_at,
    )


def _require_signal_api_token(
    *,
    authorization: str | None,
    x_api_key: str | None,
) -> None:
    expected = settings.SIGNAL_API_TOKEN.strip()
    if not expected:
        return

    provided = (x_api_key or "").strip()
    if not provided:
        raw_auth = (authorization or "").strip()
        if raw_auth.lower().startswith("bearer "):
            provided = raw_auth[7:].strip()
        else:
            provided = raw_auth

    if not provided or not hmac.compare_digest(provided, expected):
        logger.warning("Invalid signal token")
        raise HTTPException(status_code=401, detail="Invalid signal API token")


def _resolve_tier(db: Session, user: User) -> str:
    if getattr(user, "role", "user") == "admin":
        return "elite"
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    return normalize_plan(sub.plan if sub else "basic")


def _assert_symbol_access(db: Session, *, user: User, tier: str, symbol: str) -> None:
    allowed = allowed_symbols_for_plan(tier)
    selected = get_user_enabled_symbols(db, user.id, tier)
    if symbol not in allowed:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol}' is not available on your tier")
    if symbol not in selected:
        raise HTTPException(status_code=403, detail=f"Symbol '{symbol}' is not enabled in your settings")


def _latest_targets(db: Session, *, symbol: str, tier: str) -> OracleTargetsSnapshot | None:
    row = (
        db.query(OracleTargetsSnapshot)
        .filter(OracleTargetsSnapshot.symbol == symbol, OracleTargetsSnapshot.tier == tier)
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


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return _as_utc(parsed)


def _latest_h1_candle_time(db: Session, *, symbol: str) -> tuple[datetime | None, float | None]:
    row = (
        db.query(MT5Candle)
        .filter(MT5Candle.symbol == symbol, MT5Candle.timeframe == "H1")
        .order_by(MT5Candle.time_utc.desc(), MT5Candle.created_at.desc())
        .first()
    )
    if not row:
        return None, None
    return _as_utc(row.time_utc), float(row.close)


def _snapshot_latest_h1_time(snapshot: OracleTargetsSnapshot | None) -> datetime | None:
    if snapshot is None:
        return None
    state = snapshot.magnet_state if isinstance(snapshot.magnet_state, dict) else {}
    current = state.get("current") if isinstance(state.get("current"), dict) else {}
    parsed = _parse_iso_utc(current.get("h1_time_utc")) if isinstance(current.get("h1_time_utc"), str) else None
    return parsed or _as_utc(snapshot.as_of_utc)


def _ensure_fresh_targets(
    db: Session,
    *,
    symbol: str,
    tier: str,
) -> OracleTargetsSnapshot | None:
    latest = _latest_targets(db, symbol=symbol, tier=tier)
    latest_h1_time, latest_h1_close = _latest_h1_candle_time(db, symbol=symbol)
    if latest_h1_time is None:
        return latest

    snapshot_h1_time = _snapshot_latest_h1_time(latest)
    needs_recompute = latest is None or snapshot_h1_time is None or latest_h1_time > snapshot_h1_time
    if not needs_recompute:
        return latest

    try:
        refreshed = recompute_targets_snapshot(
            db,
            symbol=symbol,
            tier=tier,
            price_bid=latest_h1_close,
            price_ask=latest_h1_close,
            as_of_utc=datetime.now(timezone.utc),
            reason="api_freshness_guard",
        )
        db.commit()
        logger.info(
            "targets freshness guard recompute symbol=%s timeframe=H1 latest_candle_time=%s computed_at=%s snapshot_id=%s",
            symbol,
            latest_h1_time.isoformat(),
            _as_utc(refreshed.as_of_utc).isoformat(),
            str(refreshed.id),
        )
        return refreshed
    except Exception:
        db.rollback()
        logger.exception(
            "targets freshness guard failed symbol=%s timeframe=H1 latest_candle_time=%s",
            symbol,
            latest_h1_time.isoformat(),
        )
        return latest


# Signal ingest endpoint paths in this project:
# - /signals
# - /api/signals (legacy alias)
# - /api/v1/signals (versioned alias via API_VERSION_PREFIX)
#
# Example curl (versioned path):
# curl -X POST http://127.0.0.1:8000/api/v1/signals \
#   -H "Authorization: Bearer TOKEN" \
#   -H "Content-Type: application/json" \
#   -d '{}'
#
# Example Raspberry Pi sender snippet:
# headers = {
#     "Authorization": f"Bearer {SIGNAL_API_TOKEN}",
# }
# requests.post(
#     BACKEND_API_URL,
#     json=payload,
#     headers=headers,
# )
@router.post("", response_model=SignalCreateResult)
def ingest_signal(
    payload: SignalCreate,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
    _limit: None = rate_limit(
        "signals_ingest",
        (
            RateLimitRule(limit=300, window_seconds=60),
            RateLimitRule(limit=10_000, window_seconds=3600),
        ),
    ),
):
    _require_signal_api_token(authorization=authorization, x_api_key=x_api_key)
    try:
        row, duplicate = create_signal(db, payload=payload, bucket_seconds=60)
        db.commit()
        return SignalCreateResult(signal=_serialize_signal(row), duplicate=duplicate)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("signals ingest failed symbol=%s timeframe=%s", payload.symbol, payload.timeframe)
        raise HTTPException(status_code=500, detail="Failed to store signal") from None


@router.get("", response_model=SignalListOut)
def list_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    signal_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("signals_list", (RateLimitRule(limit=180, window_seconds=60),)),
):
    rows, total = get_signals(
        db,
        symbol=symbol,
        timeframe=timeframe,
        signal_type=signal_type,
        limit=limit,
        offset=offset,
    )
    return SignalListOut(
        items=[_serialize_signal(row) for row in rows],
        total=total,
        limit=max(min(int(limit), 200), 1),
        offset=max(int(offset), 0),
    )


@router.get("/latest", response_model=SignalListOut)
def latest_signals(
    symbol: str | None = None,
    timeframe: str | None = None,
    signal_type: str | None = None,
    limit: int = 20,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("signals_latest_feed", (RateLimitRule(limit=180, window_seconds=60),)),
):
    rows = get_latest_signals(
        db,
        symbol=symbol,
        timeframe=timeframe,
        signal_type=signal_type,
        limit=limit,
    )
    items = [_serialize_signal(row) for row in rows]
    return SignalListOut(items=items, total=len(items), limit=max(min(int(limit), 200), 1), offset=0)


@router.get("/intel/latest")
def latest_signal(
    symbol: str = "XAUUSD",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("signals_latest", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value = symbol.strip().upper()
    tier = _resolve_tier(db, user)
    _assert_symbol_access(db, user=user, tier=tier, symbol=symbol_value)

    latest_regime = (
        db.query(GoldRegimeDaily)
        .filter(GoldRegimeDaily.symbol == symbol_value)
        .order_by(GoldRegimeDaily.as_of_utc.desc(), GoldRegimeDaily.created_at.desc())
        .first()
    )
    targets = _ensure_fresh_targets(db, symbol=symbol_value, tier="pro")
    if not latest_regime and not targets:
        raise HTTPException(status_code=404, detail=f"No signal snapshot available yet for {symbol_value}")

    direction = "NO_TRADE"
    confidence = 0.0
    message = "Targets snapshot ready."
    as_of_utc = None
    daily_permission = None
    opportunity_direction = None
    h1_confirm_ok = None
    if latest_regime:
        public = latest_regime.public_factors_json if isinstance(latest_regime.public_factors_json, dict) else {}
        direction = (
            latest_regime.final_allowed_elite
            if tier == "elite" and latest_regime.final_allowed_elite
            else latest_regime.final_allowed_basic or latest_regime.allowed_direction or "NO_TRADE"
        )
        confidence = float(latest_regime.confidence or 0.0)
        message = latest_regime.notes or "Latest signal state ready."
        as_of_utc = latest_regime.as_of_utc.isoformat()
        daily_permission = public.get("daily_permission")
        opportunity_direction = public.get("opportunity_direction")
        h1_confirm_ok = public.get("confirm_ok")
        if isinstance(public.get("reason_basic"), str):
            message = str(public.get("reason_basic"))

    target_payload = None
    if targets:
        target_payload = {
            "as_of_utc": targets.as_of_utc.isoformat(),
            "tier": targets.tier,
            "timeframe_base": targets.timeframe_base,
            "price_bid": targets.price_bid,
            "price_ask": targets.price_ask,
            "magnet_price": targets.magnet_price,
            "zone_to_zone_target": targets.zone_to_zone_target,
            "sellside_liquidity": targets.sellside_liquidity,
            "buyside_liquidity": targets.buyside_liquidity,
            "magnet_state": targets.magnet_state if isinstance(targets.magnet_state, dict) else {},
        }
        if as_of_utc is None:
            as_of_utc = targets.as_of_utc.isoformat()

    return {
        "symbol": symbol_value,
        "tier": tier,
        "as_of_utc": as_of_utc,
        "allowed_direction": direction,
        "daily_permission": daily_permission,
        "opportunity_direction": opportunity_direction,
        "h1_confirm_ok": h1_confirm_ok,
        "confidence": confidence,
        "message": message,
        "targets": target_payload,
    }


@router.get("/targets/latest")
def latest_targets(
    symbol: str = "XAUUSD",
    tier: str = "pro",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("signals_targets_latest", (RateLimitRule(limit=120, window_seconds=60),)),
):
    symbol_value = symbol.strip().upper()
    requested_tier = normalize_plan(tier)
    user_tier = _resolve_tier(db, user)
    _assert_symbol_access(db, user=user, tier=user_tier, symbol=symbol_value)

    if TIER_ORDER.get(user_tier, 0) < TIER_ORDER.get(requested_tier, 0):
        raise HTTPException(status_code=403, detail=f"Tier '{requested_tier}' requires an upgrade")

    row = _ensure_fresh_targets(db, symbol=symbol_value, tier=requested_tier)
    if not row:
        raise HTTPException(status_code=404, detail=f"No targets snapshot available yet for {symbol_value}")

    magnet_state = row.magnet_state if isinstance(row.magnet_state, dict) else {}
    return {
        "symbol": row.symbol,
        "tier": row.tier,
        "timeframe_base": row.timeframe_base,
        "as_of_utc": row.as_of_utc.isoformat(),
        "price_bid": row.price_bid,
        "price_ask": row.price_ask,
        "magnet_price": row.magnet_price,
        "zone_to_zone_target": row.zone_to_zone_target,
        "sellside_liquidity": row.sellside_liquidity,
        "buyside_liquidity": row.buyside_liquidity,
        "magnet_state": magnet_state,
    }


@router.get("/{signal_id}", response_model=SignalOut)
def signal_by_id(
    signal_id: UUID,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    _limit: None = rate_limit("signals_by_id", (RateLimitRule(limit=180, window_seconds=60),)),
):
    row = get_signal_by_id(db, signal_id=signal_id)
    if not row:
        raise HTTPException(status_code=404, detail="Signal not found")
    return _serialize_signal(row)
