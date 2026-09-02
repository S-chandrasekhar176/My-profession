import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from feeds.base import BaseFeed

logger = logging.getLogger(__name__)

# Mapping of candle intervals to yfinance format
_TIMEFRAME_MAP = {
    "1m": "1m",
    "1min": "1m",
    "5m": "5m",
    "5min": "5m",
    "15m": "15m",
    "15min": "15m",
    "30m": "30m",
    "30min": "30m",
    "1h": "60m",
    "1hour": "60m",
    "60m": "60m",
    "1d": "1d",
    "1day": "1d",
    "1w": "1wk",
    "1week": "1wk",
}

# Yahoo Finance suffix for NSE
_YAHOO_NSE_SUFFIX = ".NS"

# Timeframe duration in minutes for calculating how far back to fetch
_TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1d": 1440,
    "1w": 10080,
}


class YahooHistoricalFeed(BaseFeed):
    """Historical data feed using yfinance wrapped in non-blocking asyncio threads.

    Fetches OHLCV candle data from Yahoo Finance.
    Includes in-memory TTL caching to eliminate redundant fetch requests across scans.
    """

    def __init__(self, cache_ttl_seconds: float = 45.0):
        self._connected = True
        self._cache_ttl_seconds = float(cache_ttl_seconds)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    async def connect(self) -> Dict[str, Any]:
        return {"success": True, "message": "Yahoo feed is stateless, no connection needed"}

    async def disconnect(self) -> Dict[str, Any]:
        self.clear_cache()
        return {"success": True, "message": "Yahoo feed cache cleared"}

    def clear_cache(self) -> None:
        """Clear all cached candles."""
        self._cache.clear()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Return cache hit/miss statistics."""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "cached_entries": len(self._cache),
            "ttl_seconds": self._cache_ttl_seconds,
        }

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True, "subscribed": len(symbols), "message": "Yahoo is not real-time, no subscription needed"}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True, "unsubscribed": len(symbols), "message": "Yahoo is not real-time"}

    async def get_ltp(self, symbol: str) -> float:
        """Return the last close price in a non-blocking thread."""
        def _sync_ltp() -> float:
            try:
                import yfinance as yf
                yahoo_sym = self._to_yahoo_symbol(symbol)
                ticker = yf.Ticker(yahoo_sym)
                try:
                    fast_info = getattr(ticker, "fast_info", None)
                    if fast_info:
                        last_price = getattr(fast_info, "last_price", None)
                        if last_price is None and hasattr(fast_info, "get"):
                            last_price = fast_info.get("last_price")
                        if last_price and float(last_price) > 0:
                            return round(float(last_price), 2)
                except Exception:
                    pass
                hist = ticker.history(period="1d", interval="1m")
                if hist is not None and not hist.empty:
                    return round(float(hist["Close"].iloc[-1]), 2)
                hist_daily = ticker.history(period="1d")
                if hist_daily is not None and not hist_daily.empty:
                    return round(float(hist_daily["Close"].iloc[-1]), 2)
            except Exception as e:
                logger.warning("Failed sync LTP fetch for %s: %s", symbol, e, exc_info=True)
            return 0.0

        try:
            res = await asyncio.to_thread(_sync_ltp)
            self._connected = bool(res and res > 0)
            return res
        except Exception as e:
            self._connected = False
            logger.warning("Failed to get LTP for %s from Yahoo: %s", symbol, e)
            return 0.0

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
        force_refresh: bool = False,
    ) -> List[Dict[str, Any]]:
        """Fetch historical candles from Yahoo Finance asynchronously with in-memory TTL caching."""
        import time

        yahoo_sym = self._to_yahoo_symbol(symbol)
        cache_key = f"{yahoo_sym}:{timeframe}:{count}"
        now = time.time()

        # Check in-memory cache if force_refresh is not requested
        if not force_refresh and cache_key in self._cache:
            entry = self._cache[cache_key]
            if (now - entry.get("timestamp", 0.0)) < self._cache_ttl_seconds:
                self._cache_hits += 1
                return [dict(c) for c in entry.get("candles", [])]

        self._cache_misses += 1

        def _sync_candles() -> List[Dict[str, Any]]:
            try:
                import yfinance as yf

                yf_interval = _TIMEFRAME_MAP.get(timeframe, "5m")
                tf_minutes = _TIMEFRAME_MINUTES.get(timeframe, 5)

                # Calculate period needed.
                # IMPORTANT: an NSE session is only 375 minutes (09:15-15:30),
                # so a calendar-day period holds at most 75 five-minute bars.
                # Sizing the period by calendar days (1440 min) starved early
                # intraday scans of candle history ("Insufficient candles
                # (1/20)" at the open). Size by TRADING days instead.
                NSE_SESSION_MINUTES = 375
                total_minutes = tf_minutes * count
                if total_minutes <= NSE_SESSION_MINUTES:
                    period = "1d"
                elif total_minutes <= NSE_SESSION_MINUTES * 5:
                    period = "5d"
                elif total_minutes <= NSE_SESSION_MINUTES * 22:
                    period = "1mo"
                elif total_minutes <= NSE_SESSION_MINUTES * 66:
                    period = "3mo"
                else:
                    period = "1y"

                ticker = yf.Ticker(yahoo_sym)
                hist = ticker.history(period=period, interval=yf_interval)

                if hist is None or hist.empty:
                    return []

                # Slice to requested count
                if len(hist) > count:
                    hist = hist.tail(count)

                candles = []
                for idx, row in hist.iterrows():
                    ts = idx
                    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                        ts = ts.tz_convert("Asia/Kolkata")
                    candles.append({
                        "timestamp": ts.isoformat(),
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
                        "volume": int(row["Volume"]),
                    })
                return candles
            except Exception as e:
                logger.warning("Failed sync candles fetch for %s: %s", symbol, e, exc_info=True)
                return []

        try:
            res = await asyncio.to_thread(_sync_candles)
            if res and len(res) > 0:
                self._connected = True
                stored_candles = [dict(c) for c in res]
                self._cache[cache_key] = {"candles": stored_candles, "timestamp": time.time()}
                return [dict(c) for c in stored_candles]
            self._connected = False
            return []
        except Exception as e:
            self._connected = False
            logger.error("Failed to get candles for %s: %s", symbol, e)
            return []

    async def get_historical(
        self,
        symbol: str,
        start_date: str = "",
        end_date: str = "",
        timeframe: str = "5m",
    ) -> List[Dict[str, Any]]:
        """Fetch historical candles for given symbol and date range asynchronously."""
        def _sync_hist() -> List[Dict[str, Any]]:
            try:
                import yfinance as yf

                tf_clean = timeframe.replace("min", "m").replace("hour", "h").replace("day", "d")
                yf_interval = _TIMEFRAME_MAP.get(tf_clean, _TIMEFRAME_MAP.get(timeframe, "5m"))

                yahoo_sym = self._to_yahoo_symbol(symbol.strip())
                ticker = yf.Ticker(yahoo_sym)

                # Convert dates from DD-MM-YYYY to YYYY-MM-DD if needed
                start_dt = None
                end_dt = None
                if start_date:
                    try:
                        start_dt = datetime.strptime(start_date, "%d-%m-%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        start_dt = start_date
                if end_date:
                    try:
                        end_dt = datetime.strptime(end_date, "%d-%m-%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        end_dt = end_date

                if start_dt and end_dt:
                    hist = ticker.history(start=start_dt, end=end_dt, interval=yf_interval)
                elif start_dt:
                    hist = ticker.history(start=start_dt, interval=yf_interval)
                else:
                    hist = ticker.history(period="1mo", interval=yf_interval)

                if hist is None or hist.empty:
                    hist = ticker.history(period="1mo", interval=yf_interval)

                if hist is None or hist.empty:
                    return []

                candles = []
                for idx, row in hist.iterrows():
                    ts = idx
                    if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                        ts = ts.tz_convert("Asia/Kolkata")
                    candles.append({
                        "timestamp": ts.isoformat(),
                        "open": round(float(row["Open"]), 2),
                        "high": round(float(row["High"]), 2),
                        "low": round(float(row["Low"]), 2),
                        "close": round(float(row["Close"]), 2),
                        "volume": int(row["Volume"]),
                    })
                return candles
            except Exception as e:
                logger.warning("Failed sync historical fetch for %s: %s", symbol, e, exc_info=True)
                return []

        try:
            return await asyncio.to_thread(_sync_hist)
        except Exception as e:
            logger.error("Failed to get historical candles for %s: %s", symbol, e)
            return []

    async def get_latest_price(self, symbol: str) -> float:
        """Alias for get_ltp to support engine interface."""
        return await self.get_ltp(symbol)

    def is_connected(self) -> bool:
        return self._connected

    def get_name(self) -> str:
        return "yahoo_historical"

    @staticmethod
    def _to_yahoo_symbol(symbol: str) -> str:
        """Convert NSE symbol to Yahoo Finance format."""
        clean = symbol.strip().upper()
        if clean in ("INDIAVIX", "VIX", "^INDIAVIX"):
            return "^INDIAVIX"
        if clean in ("NIFTY", "NIFTY 50", "NIFTY50", "^NSEI"):
            return "^NSEI"
        if clean in ("BANKNIFTY", "NIFTY BANK", "NIFTYBANK", "^NSEBANK"):
            return "^NSEBANK"
        if clean in ("SENSEX", "^BSESN"):
            return "^BSESN"
        if clean in ("MIDCPNIFTY", "NIFTY_MIDCAP_100.NS"):
            return "NIFTY_MIDCAP_100.NS"
        if clean in ("FINNIFTY", "NIFTY_FIN_SERVICE.NS"):
            return "NIFTY_FIN_SERVICE.NS"

        if clean.endswith(".NS") or clean.startswith("^"):
            return clean
        return f"{clean}{_YAHOO_NSE_SUFFIX}"
