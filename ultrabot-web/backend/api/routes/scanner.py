import logging
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import get_current_user, get_engine, get_repository
from db.repository import Repository
from core.engine import UltraBotEngine
from scanner.kronos.kronos_scanner import KronosScanner
from utils.indicators import calculate_rsi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scanner", tags=["scanner"])

kronos_scanner = KronosScanner()


async def _build_real_market_data(
    engine: Optional[UltraBotEngine], symbols: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Build market_data strictly from the live feed. No hardcoded/fabricated
    values — a symbol with no reachable feed is simply omitted rather than
    given a fake price, since a wrong number is worse than a missing one.
    """
    market_data: Dict[str, Dict[str, Any]] = {}
    feed = getattr(engine, "feed", None) if engine else None

    if feed is None:
        logger.warning("No live feed connected — Kronos scan returning empty (no fabricated prices)")
        return market_data

    for sym in symbols:
        try:
            candles = await feed.get_candles(sym, timeframe="5m", count=30)
            if not candles:
                continue

            df = pd.DataFrame(candles)
            ltp = float(df["close"].iloc[-1])
            prev_close = float(df["close"].iloc[-2]) if len(df) > 1 else ltp
            volume = int(df["volume"].iloc[-1])
            avg_volume = int(df["volume"].mean())
            high = float(df["high"].iloc[-1])
            low = float(df["low"].iloc[-1])
            open_ = float(df["open"].iloc[-1])
            rsi_series = calculate_rsi(df["close"])
            rsi = float(rsi_series.iloc[-1]) if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else 50.0

            market_data[sym] = {
                "ltp": ltp,
                "close": prev_close,
                "volume": volume,
                "avg_volume": avg_volume if avg_volume > 0 else volume,
                "high": high,
                "low": low,
                "open": open_,
                "rsi": rsi,
            }
        except Exception as exc:
            logger.warning("Skipping %s in Kronos scan — no real data available: %s", sym, exc)
            continue

    return market_data


@router.get("/kronos")
async def get_kronos_hotlist(
    username: str = Depends(get_current_user),
    engine: Optional[UltraBotEngine] = Depends(get_engine),
    repo: Repository = Depends(get_repository),
) -> List[Dict[str, Any]]:
    """Get the Kronos AI hotlist ranking, computed from real feed data only."""
    try:
        active_items = await repo.get_active_watchlist()
        symbols = [item.symbol for item in active_items]

        if not symbols:
            symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC"]

        market_data = await _build_real_market_data(engine, symbols)

        if not market_data:
            # No live feed / no reachable data at all — say so explicitly,
            # do not return a hotlist built on fabricated numbers.
            return []

        # Only scan symbols we actually got real data for.
        available_symbols = list(market_data.keys())

        scored_results = kronos_scanner.scan(
            watchlist_symbols=available_symbols,
            market_data=market_data,
            news_items=[],
        )

        formatted_results = []
        for rank, res in enumerate(scored_results, start=1):
            sym = res.get("symbol")
            m_data = market_data.get(sym, {})
            ltp = m_data.get("ltp", 0.0)
            close = m_data.get("close", ltp)
            chg_pct = ((ltp - close) / close * 100) if close > 0 else 0.0

            reasons = res.get("reasons", [])
            reason_str = ", ".join(reasons) if reasons else "Multi-factor breakout setup"

            formatted_results.append({
                "rank": rank,
                "symbol": sym,
                "price": round(ltp, 2),
                "changePct": round(chg_pct, 2),
                "volume": f"{m_data.get('volume', 0) // 1000}K",
                "hotness": round(res.get("score", 0.7), 2),
                "reason": reason_str,
            })

        return formatted_results

    except Exception as exc:
        logger.error("Failed to fetch Kronos hotlist: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch Kronos hotlist: {str(exc)}",
        )
