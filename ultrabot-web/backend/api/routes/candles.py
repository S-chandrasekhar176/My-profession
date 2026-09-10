"""Candles API Route for UltraBot Web.

Provides OHLCV historical and live candlestick data for TradingView / Lightweight Charts,
integrating Yahoo Finance real-time market data and connected broker feeds.
"""
import asyncio  # v0.4.21 (wave 3): MUST be importable at module scope —
# _get_fyers_quotes_broker() previously referenced asyncio.iscoroutine() in
# its cleanup without an in-scope import; the NameError was swallowed by
# `except Exception: pass` and Repository.close() was never awaited,
# leaking one aiosqlite connection per /api/live-quotes poll.
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

# v0.4.18 (Issues 10+11): shared lazily-built FyersBroker for REALTIME quotes
# (the broker's live quotes endpoint — the same tape the Fyers terminal
# shows). Kept separate from _fyers_feed_cache so chart candles and quotes
# fail independently; rebuilt when the stored token changes (daily re-login
# in Settings is picked up WITHOUT a backend restart).
_fyers_quotes_cache: Dict[str, Any] = {"broker": None, "tried": False, "token_sig": None}

# v0.4.18: micro-cache for bulk realtime quotes. The header banner polls
# every 3s from every open browser tab; without this each poll would hit the
# broker. 2.5s TTL keeps consecutive polls on one upstream call while staying
# well inside tick-freshness expectations.
_rt_quotes_cache: Dict[str, Any] = {"data": {}, "ts": 0.0}
_RT_QUOTES_TTL_SECONDS = 2.5


async def _get_fyers_quotes_broker() -> Optional[Any]:
    """Return a shared FyersBroker (valid stored token) for realtime quotes.

    Mirrors _get_fyers_chart_feed(): caches process-wide, retries a failed
    build at most once until the token signature changes (so a fresh daily
    re-login re-arms it), and never raises.
    """
    from db.database import async_session_factory
    from db.repository import Repository
    from utils.encryption import decrypt_credentials

    token_sig = None
    creds = {}
    try:
        # v0.4.21 (wave 3) — session lifecycle is owned by context managers.
        # The previous hand-rolled cleanup called asyncio.iscoroutine() WITHOUT
        # importing asyncio in this scope; the NameError was silently eaten by
        # `except Exception: pass`, repo.close() was never awaited (the
        # "coroutine 'Repository.close' was never awaited" RuntimeWarning at
        # the /api/live-quotes frame), and the AsyncSession's aiosqlite
        # connection leaked to the garbage collector on EVERY poll — the
        # header banner polls every ~3s per open tab. With NullPool each
        # leaked connection also strands its dedicated aiosqlite daemon
        # thread (the "Thread-2884 … non-checked-in connection" storm).
        # `async with` closes the session on every path — success, error,
        # cancellation. Repository.close() is idempotent (wave 2), so the
        # __aexit__ close plus the session close can never double-release.
        async with async_session_factory() as session:
            async with Repository(session) as repo:
                cred = await repo.get_broker_credentials("fyers")
                encrypted = getattr(cred, "encrypted_credentials", None)
        if cred is None or not encrypted:
            return None
        creds = decrypt_credentials(encrypted) or {}
        token_sig = str(creds.get("access_token") or "")
        if not token_sig:
            return None
    except Exception:
        return None

    cached = _fyers_quotes_cache["broker"]
    if cached is not None and _fyers_quotes_cache["token_sig"] == token_sig:
        return cached
    if _fyers_quotes_cache["tried"] and _fyers_quotes_cache["token_sig"] == token_sig:
        return None

    try:
        from brokers.fyers import FyersBroker

        app_id = str(creds.get("app_id") or creds.get("client_id") or "")
        if not app_id:
            return None
        broker = FyersBroker(app_id=app_id, access_token=token_sig)
        _fyers_quotes_cache["broker"] = broker
        _fyers_quotes_cache["token_sig"] = token_sig
        _fyers_quotes_cache["tried"] = True
        logger.info("Realtime quotes: shared FyersBroker ready (token valid)")
        return broker
    except Exception as exc:
        logger.warning("Realtime quotes: FyersBroker build failed (%s) — engine/Yahoo fallbacks", exc)
        _fyers_quotes_cache["tried"] = True
        _fyers_quotes_cache["token_sig"] = token_sig
        return None


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
    """Fetch near-real-time market quotes via Yahoo Finance for given symbols.

    v0.4.16 (user-testing feedback 2026-09-08): the previous version pulled
    DAILY bars (period=5d, interval=1d) and reported the last daily CLOSE as
    the "realtime" price — during a live session SENSEX/MIDCPNIFTY/FINNIFTY
    showed a value that could be ~24h stale and the change/direction was vs
    the PREVIOUS session, so the header banner disagreed with the live tape.
    Now: 15-minute intraday bars for the last 5 sessions — the price is the
    latest intraday close and the change is vs the PREVIOUS SESSION's close.
    Yahoo NSE quotes are delayed up to ~15 min; the source label says so.
    """
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
            interval="15m",
            group_by="ticker",
            progress=False,
            timeout=10,
        )

        for orig, y_sym in yahoo_map.items():
            try:
                sub = df[y_sym] if len(unique_tickers) > 1 else df
                sub = sub.dropna(subset=["Close"])
                if sub.empty:
                    continue
                latest = float(sub["Close"].iloc[-1])

                # Previous SESSION close = last intraday close strictly before
                # the latest bar's calendar day (the last 15m bar of the
                # previous session is effectively that day's close).
                prev = latest
                try:
                    idx_dates = sub.index.date
                    last_day = idx_dates[-1]
                    hist = sub[[d < last_day for d in idx_dates]]
                    if len(hist):
                        prev = float(hist["Close"].iloc[-1])
                except Exception:
                    prev = latest

                change = round(latest - prev, 2)
                change_pct = round((change / prev) * 100, 2) if prev > 0 else 0.0
                quotes[orig] = {
                    "price": round(latest, 2),
                    "change": change,
                    "changePct": change_pct,
                    "previousClose": round(prev, 2),
                    "source": "Yahoo (15m delayed)",
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
    global _quotes_cache, _quotes_cache_timestamp, _rt_quotes_cache

    if not symbols:
        return {"success": True, "data": {}}

    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    now = time.time()
    results: Dict[str, Any] = {}

    missing_symbols = []

    # ── Step 0 (v0.4.18, Issues 10+11): REALTIME broker quotes first ──
    # The previous chain answered the header banner from the engine's
    # scan-cadence attributes (60-180s stale) and, for every index the
    # engine does not track (SENSEX/MIDCPNIFTY/FINNIFTY), from Yahoo
    # 15-MINUTE bars — so the banner "ticks" were minutes old and users
    # saw simulated-feeling movement. Now the broker's live quotes
    # endpoint (price+change+changePct+prev_close in one bulk call,
    # micro-cached 2.5s) is the primary source whenever Fyers
    # credentials are stored.
    rt_broker = await _get_fyers_quotes_broker()
    if rt_broker is not None and sym_list:
        rt_data: Dict[str, Dict[str, Any]] = {}
        cache_fresh = (now - _rt_quotes_cache["ts"]) < _RT_QUOTES_TTL_SECONDS
        if cache_fresh and _rt_quotes_cache["data"]:
            rt_data = _rt_quotes_cache["data"]
        else:
            try:
                rt_data = await asyncio.wait_for(
                    rt_broker.get_quotes(sym_list), timeout=4.0
                )
                if rt_data:
                    _rt_quotes_cache["data"] = rt_data
                    _rt_quotes_cache["ts"] = now
            except Exception as rt_exc:
                logger.debug("Realtime quotes fetch failed (%s) — fallbacks", rt_exc)
                rt_data = {}
        for sym in sym_list:
            q = rt_data.get(sym)
            if q and q.get("price", 0) > 0:
                results[sym] = {
                    "price": q["price"],
                    "change": q.get("change", 0.0),
                    "changePct": q.get("changePct", 0.0),
                    "previousClose": q.get("previousClose"),
                    "source": "Fyers (Realtime)",
                }

    # 1. Check if engine has live quotes for indices or watchlist stocks
    active_broker = getattr(engine, "broker_name", "") or "paper"
    for sym in sym_list:
        if sym in results:
            continue
        clean = sym.replace(".NS", "").replace("^", "")
        # Check special engine indices
        # v0.4.16 (user-testing feedback 2026-09-08): engine.nifty_change is a
        # PERCENT, but the old response wrote it into BOTH `change` (points)
        # and `changePct` — the banner showed e.g. "−0.14 pts / −0.14%" while
        # the real move was −23 pts. Derive points from the percentage.
        if clean in ("NIFTY", "NIFTY50") and engine and getattr(engine, "nifty_price", 0) > 0:
            _nifty_price = round(float(engine.nifty_price), 2)
            _nifty_pct = float(getattr(engine, "nifty_change", 0.0) or 0.0)
            _denom = 1.0 + _nifty_pct / 100.0
            _nifty_prev = (_nifty_price / _denom) if _denom else _nifty_price
            results[clean] = {
                "price": _nifty_price,
                "change": round(_nifty_price - _nifty_prev, 2),
                "changePct": round(_nifty_pct, 2),
                "source": f"{active_broker.capitalize()}",
            }
            continue
        # BANKNIFTY/VIX have no engine-side change figure — 0.0 renders as a
        # neutral "—" on the banner rather than a fabricated direction.
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
