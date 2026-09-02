"""Candles API Route for UltraBot Web.

Provides OHLCV historical and live candlestick data for TradingView / Lightweight Charts,
integrating Yahoo Finance real-time market data and connected broker feeds.
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_engine
from core.engine import UltraBotEngine
from feeds.yahoo_historical import YahooHistoricalFeed
from utils.indicators import (
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_atr,
)

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

router = APIRouter(tags=["candles", "quotes"])

_quotes_cache: Dict[str, Dict[str, Any]] = {}
_quotes_cache_timestamp: float = 0.0

# v0.4.8 (hotfix #3): lazily-built shared FyersCandleFeed for chart requests
# that explicitly ask for broker=fyers (or auto). Built once and reused so we
# don't construct a new Fyers client per request; the module-level Fyers rate
# limiters in brokers.fyers still apply to every call.
_fyers_feed_cache: Dict[str, Any] = {"feed": None, "tried": False}


async def _get_fyers_chart_feed() -> Optional[Any]:
    """Return a shared FyersCandleFeed when a valid Fyers token exists, else None.

    Result is cached process-wide; a None result is retried at most once per
    process lifetime unless the token status changes (the feed itself rebuilds
    its broker client on auth failures).
    """
    if _fyers_feed_cache["feed"] is not None:
        return _fyers_feed_cache["feed"]
    if _fyers_feed_cache["tried"]:
        return None
    _fyers_feed_cache["tried"] = True
    try:
        from db.database import async_session_factory
        from db.repository import Repository
        from feeds.fyers_candles import build_fyers_candle_feed

        async def _repo_getter():
            session = async_session_factory()
            return Repository(session)

        feed = await build_fyers_candle_feed(_repo_getter)
        _fyers_feed_cache["feed"] = feed
        if feed is not None:
            logger.info("Chart feed: FyersCandleFeed constructed for broker=fyers chart requests")
        return feed
    except Exception as feed_exc:
        logger.warning("Chart feed: FyersCandleFeed unavailable (%s) — falling back to Yahoo", feed_exc)
        return None

INDEX_SYMBOL_MAP = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "SENSEX": "^BSESN",
    "BSESN": "^BSESN",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYBANK": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "MIDCPNIFTY": "NIFTY_MIDCAP_100.NS",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "VIX": "^INDIAVIX",
    "INDIAVIX": "^INDIAVIX",
    "INDIA VIX": "^INDIAVIX",
}


def _to_yahoo_ticker(sym: str) -> str:
    s = sym.strip().upper()
    if s in INDEX_SYMBOL_MAP:
        return INDEX_SYMBOL_MAP[s]
    if s.startswith("^"):
        return s
    if not s.endswith(".NS") and not s.endswith(".BO"):
        return f"{s}.NS"
    return s


def _fetch_realtime_quotes_sync(symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    """Fetch 100% real-time market quotes via Yahoo Finance for given symbols."""
    import yfinance as yf

    if not symbols:
        return {}

    yahoo_map = {orig: _to_yahoo_ticker(orig) for orig in symbols}
    unique_tickers = list(set(yahoo_map.values()))

    quotes = {}
    try:
        df = yf.download(
            tickers=" ".join(unique_tickers),
            period="5d",
            interval="1d",
            group_by="ticker",
            progress=False,
            timeout=10,
        )

        for orig, y_sym in yahoo_map.items():
            try:
                sub = df[y_sym] if len(unique_tickers) > 1 else df
                sub = sub.dropna(subset=["Close"])
                if len(sub) >= 2:
                    latest = float(sub["Close"].iloc[-1])
                    prev = float(sub["Close"].iloc[-2])
                    change = round(latest - prev, 2)
                    change_pct = round((change / prev) * 100, 2) if prev > 0 else 0.0
                    quotes[orig] = {
                        "price": round(latest, 2),
                        "change": change,
                        "changePct": change_pct,
                        "previousClose": round(prev, 2),
                        "source": "Yahoo Realtime Feed",
                    }
                elif len(sub) == 1:
                    latest = float(sub["Close"].iloc[-1])
                    quotes[orig] = {
                        "price": round(latest, 2),
                        "change": 0.0,
                        "changePct": 0.0,
                        "previousClose": round(latest, 2),
                        "source": "Yahoo Realtime Feed",
                    }
            except Exception as parse_err:
                logger.debug("Could not parse sub dataframe for %s (%s): %s", orig, y_sym, parse_err)
    except Exception as exc:
        logger.error("Failed batch realtime quote fetch: %s", exc)

    return quotes


def _to_unix_timestamp(ts: Any) -> int:
    """Convert ISO timestamp string or datetime object to Unix epoch seconds."""
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            return int(dt.timestamp())
        except Exception:
            try:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                return int(dt.replace(tzinfo=IST).timestamp())
            except Exception:
                pass
    if isinstance(ts, datetime):
        return int(ts.timestamp())
    return int(datetime.now(IST).timestamp())


@router.get("/api/live-quotes", status_code=status.HTTP_200_OK)
@router.get("/live-quotes", status_code=status.HTTP_200_OK)
async def get_live_quotes(
    symbols: str = Query(..., description="Comma-separated stock or index symbols e.g. NIFTY,SENSEX,RELIANCE"),
    engine: Optional[UltraBotEngine] = Depends(get_engine),
) -> Dict[str, Any]:
    """Return real-time LTP, change, and change percentage for requested symbols directly from connected broker feeds or live market data."""
    import time
    import asyncio
    global _quotes_cache, _quotes_cache_timestamp

    if not symbols:
        return {"success": True, "data": {}}

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    now = time.time()
    results: Dict[str, Any] = {}

    missing_symbols = []
    
    # 1. Check if engine has live quotes for indices or watchlist stocks
    active_broker = getattr(engine, "broker_name", "") or "paper"
    for sym in sym_list:
        clean = sym.replace(".NS", "").replace("^", "")
        # Check special engine indices
        if clean in ("NIFTY", "NIFTY50") and engine and getattr(engine, "nifty_price", 0) > 0:
            results[clean] = {
                "price": round(engine.nifty_price, 2),
                "change": round(getattr(engine, "nifty_change", 0.0), 2),
                "changePct": round(getattr(engine, "nifty_change", 0.0), 2),
                "source": f"{active_broker.capitalize()}",
            }
            continue
        if clean in ("BANKNIFTY", "NIFTYBANK") and engine and getattr(engine, "banknifty_price", 0) > 0:
            results[clean] = {
                "price": round(engine.banknifty_price, 2),
                "change": 0.0,
                "changePct": 0.0,
                "source": f"{active_broker.capitalize()}",
            }
            continue
        if clean in ("VIX", "INDIAVIX") and engine and getattr(engine, "vix", 0) > 0:
            results[clean] = {
                "price": round(engine.vix, 2),
                "change": 0.0,
                "changePct": 0.0,
                "source": f"{active_broker.capitalize()}",
            }
            continue

        # Use cache if fresh (< 4 seconds for real-time responsiveness)
        if clean in _quotes_cache and (now - _quotes_cache_timestamp < 4.0):
            results[clean] = _quotes_cache[clean]
        else:
            missing_symbols.append(clean)

    # 2. Try direct broker quote if engine broker is connected
    broker_missing = []
    if missing_symbols and engine and hasattr(engine, "broker") and engine.broker is not None and hasattr(engine.broker, "get_latest_price"):
        for clean in missing_symbols:
            try:
                b_price = await engine.broker.get_latest_price(clean)
                if b_price and b_price > 0:
                    q = {
                        "price": round(b_price, 2),
                        "change": 0.0,
                        "changePct": 0.0,
                        "source": f"{active_broker.capitalize()} (Direct)",
                    }
                    results[clean] = q
                    _quotes_cache[clean] = q
                else:
                    broker_missing.append(clean)
            except Exception:
                broker_missing.append(clean)
    else:
        broker_missing = missing_symbols

    # 3. Fallback to Yahoo Finance for any remaining missing symbols
    if broker_missing:
        try:
            fetched_quotes = await asyncio.wait_for(
                asyncio.to_thread(_fetch_realtime_quotes_sync, broker_missing),
                timeout=5.0,
            )
            for clean, q in fetched_quotes.items():
                results[clean] = q
                _quotes_cache[clean] = q
            _quotes_cache_timestamp = now
        except Exception as timeout_err:
            logger.debug("Live quotes fallback fetch timeout: %s", timeout_err)
            # Use existing cache or default if timeout occurs
            for clean in broker_missing:
                if clean in _quotes_cache:
                    results[clean] = _quotes_cache[clean]

    return {
        "success": True,
        "data": results,
    }


@router.get("/api/candles", status_code=status.HTTP_200_OK)
@router.get("/candles", status_code=status.HTTP_200_OK)
async def get_chart_candles(
    symbol: str = Query(..., description="Stock or index symbol e.g. RELIANCE, INFY, NIFTY"),
    timeframe: str = Query(default="5m", description="Candle timeframe e.g. 1m, 5m, 15m, 30m, 1h, 1d"),
    broker: str = Query(default="auto", description="Data feed source: auto (resolve from active broker), yahoo, angel_one, shoonya, dhan, fyers, kite"),
    count: int = Query(default=150, ge=10, le=1000, description="Number of candles to return"),
) -> Dict[str, Any]:
    """Fetch real-time and historical candlestick data for charts and paper trading."""
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Symbol parameter is required",
        )

    # Normalize index names
    if clean_symbol in ("NIFTY", "NIFTY 50", "NIFTY50"):
        clean_symbol = "^NSEI"
    elif clean_symbol in ("BANKNIFTY", "NIFTY BANK", "NIFTYBANK"):
        clean_symbol = "^NSEBANK"
    elif clean_symbol in ("INDIAVIX", "VIX", "INDIA VIX"):
        clean_symbol = "^INDIAVIX"

    feed = YahooHistoricalFeed()
    raw_candles: List[Dict[str, Any]] = []
    current_price: float = 0.0
    actual_broker = "yahoo"  # v0.4.8 (hotfix #3): report the source we ACTUALLY used

    # v0.4.8 (hotfix #3): honor broker=fyers / broker=auto when a valid Fyers
    # token exists and the timeframe is one the FyersCandleFeed natively
    # serves (1m/5m — it aggregates its 1m history to 5m). Any other requested
    # timeframe falls back to Yahoo and the response reports "yahoo" honestly
    # instead of echoing the requested broker (the previous behaviour, which
    # mislabeled Yahoo data as broker-specific).
    fyers_tf_ok = timeframe.lower() in ("1m", "5m", "1min", "5min")

    # v0.4.8 (hotfix #5): broker=auto resolves the ACTIVE broker instead of
    # defaulting to Yahoo. Priority: (1) the running engine's broker, (2)
    # settings.engine.default_broker (set via PUT /api/brokers/active), (3)
    # empty → Yahoo. Data follows the selected broker; Yahoo is only the
    # universal fallback when the broker's feed is unavailable/unwired.
    def _resolve_auto_broker() -> str:
        try:
            eng = get_engine()
            name = str(getattr(eng, "broker_name", "") or "")
            if name and name != "paper":
                return name
        except Exception:
            pass
        try:
            from config.settings import settings
            name = str((settings._raw_config.get("engine", {}) or {}).get("default_broker", "") or "")
            if name and name != "paper":
                return name
        except Exception:
            pass
        return ""

    requested = broker.strip().lower()
    if requested == "auto":
        resolved = _resolve_auto_broker()
        requested = resolved or "yahoo"
        logger.debug("Chart broker=auto resolved to '%s'", requested)

    want_fyers = requested in ("fyers",) and fyers_tf_ok
    fyers_chart_feed = await _get_fyers_chart_feed() if want_fyers else None
    if fyers_chart_feed is not None:
        try:
            raw_candles = await fyers_chart_feed.get_candles(clean_symbol, timeframe=timeframe, count=count)
            if raw_candles:
                actual_broker = "fyers"
                current_price = await fyers_chart_feed.get_ltp(clean_symbol)
        except Exception as fyers_exc:
            logger.warning("Fyers chart fetch failed for %s (%s) — Yahoo fallback: %s",
                           clean_symbol, timeframe, fyers_exc)
            raw_candles = []

    # Broker-specific feeds that are not yet wired (angel_one, shoonya, dhan,
    # zerodha, upstox) fall through to Yahoo here — honest labeling reports
    # the actual source. Wiring them is the broker-driven feed factory item.
    if not raw_candles:
        try:
            # Fetch candles via Yahoo Historical Feed
            raw_candles = await feed.get_candles(clean_symbol, timeframe=timeframe, count=count)

            # If specific symbol failed (e.g. index prefix without ^), try fallback
            if not raw_candles and not clean_symbol.startswith("^") and not clean_symbol.endswith(".NS"):
                raw_candles = await feed.get_candles(f"{clean_symbol}.NS", timeframe=timeframe, count=count)

            # Get latest real-time LTP
            current_price = await feed.get_ltp(clean_symbol)
            actual_broker = "yahoo"
            if current_price <= 0 and raw_candles:
                current_price = float(raw_candles[-1].get("close", 0.0))

        except Exception as exc:
            logger.error("Error fetching candles for %s: %s", clean_symbol, exc, exc_info=True)
            raw_candles = []

    # If no candles could be fetched, return empty formatted response
    if not raw_candles:
        return {
            "success": False,
            "symbol": symbol,
            "timeframe": timeframe,
            "broker": actual_broker,
            "requested_broker": broker,
            "currentPrice": current_price,
            "candles": [],
            "indicators": {},
            "message": f"No chart data available for symbol '{symbol}'",
        }

    # Format candles for Lightweight Charts (requires time in seconds, sorted ascending)
    formatted_candles = []
    seen_times = set()

    for c in raw_candles:
        raw_time = c.get("timestamp") or c.get("time")
        unix_time = _to_unix_timestamp(raw_time)
        if unix_time in seen_times:
            continue
        seen_times.add(unix_time)

        open_p = float(c.get("open", 0.0))
        high_p = float(c.get("high", open_p))
        low_p = float(c.get("low", open_p))
        close_p = float(c.get("close", open_p))
        volume = int(c.get("volume", 0))

        formatted_candles.append({
            "time": unix_time,
            "open": open_p,
            "high": high_p,
            "low": low_p,
            "close": close_p,
            "volume": volume,
        })

    # Sort strictly by time ascending
    formatted_candles.sort(key=lambda x: x["time"])

    # Compute live technical indicators from candles
    indicators_dict: Dict[str, Any] = {}
    try:
        closes = pd.Series([c["close"] for c in formatted_candles])
        highs = pd.Series([c["high"] for c in formatted_candles])
        lows = pd.Series([c["low"] for c in formatted_candles])

        if len(closes) >= 20:
            sma20 = calculate_sma(closes, period=20)
            indicators_dict["sma20"] = round(float(sma20.iloc[-1]), 2) if not sma20.empty and pd.notna(sma20.iloc[-1]) else None
            
            upper, mid, lower = calculate_bollinger_bands(closes, period=20, std_dev=2.0)
            indicators_dict["bb_upper"] = round(float(upper.iloc[-1]), 2) if not upper.empty and pd.notna(upper.iloc[-1]) else None
            indicators_dict["bb_middle"] = round(float(mid.iloc[-1]), 2) if not mid.empty and pd.notna(mid.iloc[-1]) else None
            indicators_dict["bb_lower"] = round(float(lower.iloc[-1]), 2) if not lower.empty and pd.notna(lower.iloc[-1]) else None

        if len(closes) >= 50:
            sma50 = calculate_sma(closes, period=50)
            indicators_dict["sma50"] = round(float(sma50.iloc[-1]), 2) if not sma50.empty and pd.notna(sma50.iloc[-1]) else None

        if len(closes) >= 14:
            rsi = calculate_rsi(closes, period=14)
            indicators_dict["rsi"] = round(float(rsi.iloc[-1]), 2) if not rsi.empty and pd.notna(rsi.iloc[-1]) else None

            atr = calculate_atr(highs, lows, closes, period=14)
            indicators_dict["atr"] = round(float(atr.iloc[-1]), 2) if not atr.empty and pd.notna(atr.iloc[-1]) else None
    except Exception as ind_exc:
        logger.debug("Failed computing technical indicators: %s", ind_exc)

    return {
        "success": True,
        "symbol": symbol,
        "timeframe": timeframe,
        "broker": actual_broker,
        "requested_broker": broker,
        "currentPrice": current_price,
        "count": len(formatted_candles),
        "candles": formatted_candles,
        "indicators": indicators_dict,
    }
