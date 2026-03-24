from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.subscription import require_tier
from app.db.models import User

router = APIRouter(prefix="/oracle", tags=["oracle (elite)"])

# ✅ Elite-only: Multi-timeframe pack
@router.get("/elite/liquidity-pack")
def oracle_liquidity_pack(
    db: Session = Depends(get_db),
    user: User = Depends(require_tier("elite")),
):
    # Example payload - replace with your real engine outputs
    return {
        "tier": "elite",
        "pack": {
            "M15": [],
            "H1": [],
            "H4": [],
            "D1": [],
        },
    }


# ✅ Elite-only: Multi-symbol (XAUUSD, GBPJPY, BTCUSD) bundle
@router.get("/elite/multi-symbol")
def oracle_multi_symbol(
    db: Session = Depends(get_db),
    user: User = Depends(require_tier("elite")),
):
    return {
        "tier": "elite",
        "symbols": {
            "XAUUSD": {"H1": [], "M15": []},
            "GBPJPY": {"H1": [], "M15": []},
            "BTCUSD": {"H1": [], "M15": []},
        },
    }


# ✅ Elite-only: “Oracle Signal Stream” (premium alerts)
@router.get("/elite/signal-stream")
def oracle_signal_stream(
    db: Session = Depends(get_db),
    user: User = Depends(require_tier("elite")),
):
    return {
        "tier": "elite",
        "signals": [
            # example
            # {"symbol":"XAUUSD","tf":"H1","bias":"bullish","entry":..., "sl":..., "tp":[...]}
        ],
    }
