import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.dependencies import get_current_user, get_repository
from db.repository import Repository
from models.watchlist_item import WatchlistItemCreate, WatchlistItemResponse
from utils.market_utils import FNO_UNIVERSE

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])

# Extra manual-add picks beyond the core scanner universe. Every symbol was
# validated live against NSE/Zerodha reference data on 2026-08-27 (Phase 5
# instrument hygiene — evidence/p5_universe_liveness.json). Removed as dead:
# TATAMOTORS (delisted, demerger), ZOMATO (renamed ETERNAL), M_M (invalid —
# real symbol M&M is in the core universe), HDBANK, TATAMETALI, MCDOWELL-N
# (renamed UNITDSPR — kept under its new symbol below).
_EXTRA_PICKS = [
    ("ETERNAL", "Eternal Ltd (formerly Zomato)"),
    ("UNITDSPR", "United Spirits Ltd"),
    ("NESTLEIND", "Nestle India Ltd"),
    ("MARICO", "Marico Ltd"),
    ("HAL", "Hindustan Aeronautics Ltd"),
    ("BEL", "Bharat Electronics Ltd"),
    ("LICI", "Life Insurance Corp"),
    ("ADANIGREEN", "Adani Green Energy"),
    ("TRENT", "Trent Ltd"),
    ("WELSPUNLIV", "Welspun Living Ltd"),
    ("BERGEPAINT", "Berger Paints India"),
    ("SIEMENS", "Siemens Ltd"),
    ("ABBOTINDIA", "Abbott India Ltd"),
    ("DLF", "DLF Ltd"),
    ("PNB", "Punjab National Bank"),
    ("CANBK", "Canara Bank"),
    ("BANKBARODA", "Bank of Baroda"),
    ("IDFCFIRSTB", "IDFC First Bank"),
    ("MUTHOOTFIN", "Muthoot Finance Ltd"),
    ("YESBANK", "Yes Bank Ltd"),
    ("INDIGO", "IndiGo Airlines"),
    ("IRFC", "IRFC Ltd"),
    ("RVNL", "RVNL"),
    ("IRCTC", "IRCTC Ltd"),
    ("TATAPOWER", "Tata Power Company"),
    ("SUZLON", "Suzlon Energy Ltd"),
    ("NHPC", "NHPC Ltd"),
    ("JPPOWER", "Jaiprakash Power Ventures"),
    ("ADANIPOWER", "Adani Power Ltd"),
    ("TATAELXSI", "Tata Elxsi Ltd"),
    ("BATAINDIA", "Bata India Ltd"),
    ("VBL", "Varun Beverages Ltd"),
    ("LAURUSLABS", "Laurus Labs Ltd"),
    ("BALRAMCHIN", "Balrampur Chini Mills"),
    ("TRIDENT", "Trident Ltd"),
    ("IGL", "Indraprastha Gas Ltd"),
    ("MGL", "Mahanagar Gas Ltd"),
    ("GAIL", "GAIL (India) Ltd"),
    ("PETRONET", "Petronet LNG Ltd"),
    ("NATIONALUM", "National Aluminium"),
    ("NMDC", "NMDC Ltd"),
    ("SAIL", "Steel Authority of India"),
    ("HINDCOPPER", "Hindustan Copper Ltd"),
    ("JINDALSTEL", "Jindal Steel & Power"),
    ("SHRIRAMFIN", "Shriram Finance Ltd"),
]


@router.get("")
async def get_watchlist(
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> List[Dict[str, Any]]:
    """Get all active watchlist items."""
    try:
        items = await repo.get_active_watchlist()
        return [
            {
                "id": item.id,
                "symbol": item.symbol,
                "name": item.name,
                "sector": item.sector,
                "lot_size": item.lot_size,
                "is_fno": item.is_fno,
                "is_active": item.is_active,
                "added_at": item.added_at,
                "last_scanned_at": item.last_scanned_at,
                "last_signal_at": item.last_signal_at,
            }
            for item in items
        ]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to get watchlist: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch watchlist: {str(exc)}",
        )


@router.post("/add")
async def add_to_watchlist(
    body: WatchlistItemCreate,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Manually add a stock to the watchlist."""
    try:
        # Check if already exists
        existing = await repo.get_watchlist_item_by_symbol(body.symbol)
        if existing is not None:
            # Re-activate if inactive
            if not existing.is_active:
                await repo.update_watchlist_item(existing.id, is_active=True)
                return {
                    "message": f"'{body.symbol}' re-activated in watchlist",
                    "id": existing.id,
                    "symbol": body.symbol,
                }
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"'{body.symbol}' already exists in watchlist",
            )

        item = await repo.add_watchlist_item(
            symbol=body.symbol,
            name=body.name,
            sector=body.sector,
            lot_size=body.lot_size,
            is_fno=body.is_fno,
            is_active=body.is_active,
            extra=body.extra,
        )

        return {
            "message": f"'{body.symbol}' added to watchlist",
            "id": item.id,
            "symbol": item.symbol,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to add watchlist item: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add watchlist item: {str(exc)}",
        )


@router.delete("/{symbol}")
async def remove_from_watchlist(
    symbol: str,
    username: str = Depends(get_current_user),
    repo: Repository = Depends(get_repository),
) -> Dict[str, Any]:
    """Remove a stock from the watchlist (deactivate it)."""
    try:
        # Find by symbol
        item = await repo.get_watchlist_item_by_symbol(symbol)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"'{symbol}' not found in watchlist",
            )

        # Deactivate rather than delete
        await repo.update_watchlist_item(item.id, is_active=False)

        return {
            "message": f"'{symbol}' removed from watchlist",
            "id": item.id,
            "symbol": symbol,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to remove watchlist item: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove watchlist item: {str(exc)}",
        )


@router.get("/universe")
async def get_fno_universe(
    username: str = Depends(get_current_user),
) -> List[Dict[str, str]]:
    """Return all tradable picks: core scanner universe + validated extras.

    Single source of truth: the core list is utils.market_utils.FNO_UNIVERSE
    (the same universe the scanner scans), so this dropdown can never drift
    from what the engine actually trades.
    """
    core = [(s["symbol"], s["name"]) for s in FNO_UNIVERSE]
    core_syms = {sym for sym, _ in core}
    extras = [(sym, name) for sym, name in _EXTRA_PICKS if sym not in core_syms]
    return [
        {"symbol": sym, "name": name}
        for sym, name in core + extras
    ]
