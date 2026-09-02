"""Fyers 1-minute realtime candle feed (P1 — data foundation).

When the user connects their Fyers account (valid daily token), this feed
replaces Yahoo as the PRIMARY candle source:

* ``get_candles(timeframe="5m")`` fetches native 1-minute bars from the
  Fyers history API and aggregates them into 5-minute OHLCV — the strategy
  layer keeps consuming exactly the same candle format it does today
  (timestamp = IST ISO string of the bar OPEN, matching YahooHistoricalFeed)
  but with ~realtime freshness instead of Yahoo's 1–3 minute lag.
* ``get_candles(timeframe="1m")`` returns the raw 1-minute bars — the
  foundation for the Phase-3 scalping suite and 1-minute backtests.
* ``get_ltp`` uses the newest 1-minute close (minute-level realtime).

Failure behaviour: returns [] on error, letting FeedManager count failures
and switch to the Yahoo backup feed after its threshold — the engine never
loses data. When a call returns empty twice in a row, the broker client is
rebuilt from the (possibly re-freshed) stored credentials, so a daily
re-login performed in Settings is picked up WITHOUT a backend restart.

Rate limits (Fyers data endpoints: 10/s, 200/min, 100k/day) comfortably
absorb a 60-second scan cadence over a 10–20 symbol watchlist.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from feeds.base import BaseFeed

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

# How long a fetched candle set stays fresh enough to serve from cache.
_CACHE_TTL_SECONDS = 20.0
# Consecutive empty results before rebuilding the broker client from DB creds.
_EMPTY_RESULTS_BEFORE_REBUILD = 2
# Candle history window (calendar days). A 100-bar 5m request needs ~500
# 1m bars ≈ 1.5 trading days; 5 calendar days covers holidays comfortably.
_HISTORY_WINDOW_DAYS = 5

# Engine/plain symbols → Fyers instruments.
_INDEX_MAP = {
    "^NSEI": "NSE:NIFTY50-INDEX",
    "NIFTY": "NSE:NIFTY50-INDEX",
    "NIFTY50": "NSE:NIFTY50-INDEX",
    "^NSEBANK": "NSE:NIFTYBANK-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "NIFTYBANK": "NSE:NIFTYBANK-INDEX",
    "FINNIFTY": "NSE:FINNIFTY-INDEX",
    "MIDCPNIFTY": "NSE:MIDCPNIFTY-INDEX",
    "^INDIAVIX": "NSE:INDIAVIX-INDEX",
    "INDIAVIX": "NSE:INDIAVIX-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
}


def to_fyers_symbol(symbol: str) -> str:
    """Map an engine symbol (RELIANCE / NIFTY / ^NSEI / NSE:INFY-EQ) to the
    Fyers instrument identifier."""
    s = (symbol or "").strip().upper()
    if s in _INDEX_MAP:
        return _INDEX_MAP[s]
    if ":" in s:
        return s  # already a Fyers-style symbol
    return f"NSE:{s}-EQ"


def _epoch_to_ist_iso(epoch_seconds: float) -> str:
    """Fyers epoch (seconds) → IST ISO string, matching Yahoo feed format."""
    return datetime.fromtimestamp(float(epoch_seconds), tz=IST).isoformat()


def aggregate_1m_to_5m(one_min: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate 1-minute OHLCV bars into 5-minute bars.

    Buckets are aligned to 5-minute boundaries of the epoch clock (which is
    exactly how NSE 5-minute candles are stamped). Each bucket keeps the
    bar-open timestamp, first open, max high, min low, last close, summed
    volume. Input order does not matter (bars are sorted by timestamp
    first); output is chronologically sorted.
    """
    bars_sorted = sorted(
        (c for c in one_min if c.get("timestamp")),
        key=lambda c: float(c["timestamp"]),
    )
    buckets: Dict[int, Dict[str, Any]] = {}
    for c in bars_sorted:
        ts = float(c.get("timestamp", 0) or 0)
        if ts <= 0:
            continue
        bucket_start = int(ts) // 300 * 300
        b = buckets.get(bucket_start)
        if b is None:
            buckets[bucket_start] = {
                "timestamp": _epoch_to_ist_iso(bucket_start),
                "open": float(c.get("open", 0)),
                "high": float(c.get("high", 0)),
                "low": float(c.get("low", 0)),
                "close": float(c.get("close", 0)),
                "volume": float(c.get("volume", 0) or 0),
            }
        else:
            b["high"] = max(b["high"], float(c.get("high", 0)))
            b["low"] = min(b["low"], float(c.get("low", 0)))
            b["close"] = float(c.get("close", 0))
            b["volume"] += float(c.get("volume", 0) or 0)

    return [buckets[k] for k in sorted(buckets.keys())]


class FyersCandleFeed(BaseFeed):
    """Primary candle feed backed by the Fyers 1-minute history API."""

    is_realtime = True  # engine uses this to shorten the scan cadence

    def __init__(
        self,
        broker_factory: Callable[[], Any],
        repo_factory: Optional[Callable[[], Any]] = None,
        cache_ttl_seconds: float = _CACHE_TTL_SECONDS,
    ):
        """
        Args:
            broker_factory: zero-arg callable returning a FyersBroker-like
                object with ``get_candles(symbol, resolution, range_from,
                range_to)``. Called at construction AND on self-heal rebuilds.
            repo_factory: optional async zero-arg callable returning a
                repository; used by ``build_fyers_candle_feed`` only — the
                feed itself never talks to the DB directly.
        """
        self._broker_factory = broker_factory
        self._broker = broker_factory()
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = float(cache_ttl_seconds)
        self._consecutive_empty = 0
        self._connected = True  # optimistic; failures flip it until a rebuild

    # ── Hot-swap (daily re-login) ─────────────────────────────

    def apply_new_token(self, access_token: str) -> None:
        """Hot-apply a fresh Fyers access token (called by the re-login flow
        after the browser OAuth completes) without dropping cached candles."""
        if not access_token:
            return
        try:
            self._broker.access_token = access_token
            # Force SDK client rebuild on next call — FyersBroker caches its
            # client (self._client) keyed on the token at build time.
            self._broker._client = None
            self._consecutive_empty = 0
            self._connected = True
            logger.info("FyersCandleFeed: applied fresh access token")
        except Exception as exc:
            logger.warning("FyersCandleFeed: could not apply new token: %s", exc)

    def _maybe_rebuild_broker(self) -> None:
        """Rebuild the broker from stored credentials (may have been
        re-freshed by a re-login) after repeated empty responses."""
        try:
            self._broker = self._broker_factory()
            self._consecutive_empty = 0
            self._connected = True
            logger.info("FyersCandleFeed: rebuilt broker client from stored credentials")
        except Exception as exc:
            logger.warning("FyersCandleFeed: broker rebuild failed: %s", exc)

    # ── Candle fetching ───────────────────────────────────────

    async def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        tf = (timeframe or "5m").lower()
        cache_key = f"{symbol}:{tf}:{count}"
        now = time.time()
        cached = self._cache.get(cache_key)
        if cached and (now - cached["ts"]) < self._cache_ttl:
            return [dict(c) for c in cached["candles"]]

        try:
            fyers_sym = to_fyers_symbol(symbol)
            range_to = datetime.now(IST).date().isoformat()
            range_from = (datetime.now(IST) - timedelta(days=_HISTORY_WINDOW_DAYS)).date().isoformat()

            # FyersBroker.get_candles(symbol, exchange, resolution, from, to)
            raw = await self._broker.get_candles(
                fyers_sym,
                resolution="1",
                range_from=range_from,
                range_to=range_to,
            )
            if not raw:
                self._consecutive_empty += 1
                self._connected = False
                if self._consecutive_empty >= _EMPTY_RESULTS_BEFORE_REBUILD:
                    self._maybe_rebuild_broker()
                return []

            self._consecutive_empty = 0
            self._connected = True

            # Normalize: epoch ts → IST ISO (Yahoo-compatible format)
            one_min = [
                {
                    "timestamp": c.get("timestamp"),
                    "open": float(c.get("open", 0) or 0),
                    "high": float(c.get("high", 0) or 0),
                    "low": float(c.get("low", 0) or 0),
                    "close": float(c.get("close", 0) or 0),
                    "volume": float(c.get("volume", 0) or 0),
                }
                for c in raw
            ]
            one_min = [c for c in one_min if c["timestamp"]]
            one_min.sort(key=lambda c: float(c["timestamp"]))

            if tf in ("1m", "1min", "1"):
                out = [
                    {
                        "timestamp": _epoch_to_ist_iso(c["timestamp"]),
                        "open": round(c["open"], 2),
                        "high": round(c["high"], 2),
                        "low": round(c["low"], 2),
                        "close": round(c["close"], 2),
                        "volume": int(c["volume"]),
                    }
                    for c in one_min
                ]
            else:
                agg = aggregate_1m_to_5m(one_min)
                out = [
                    {
                        "timestamp": c["timestamp"],
                        "open": round(c["open"], 2),
                        "high": round(c["high"], 2),
                        "low": round(c["low"], 2),
                        "close": round(c["close"], 2),
                        "volume": int(c["volume"]),
                    }
                    for c in agg
                ]

            out = out[-count:] if count and len(out) > count else out
            self._cache[cache_key] = {"candles": [dict(c) for c in out], "ts": time.time()}
            return out
        except Exception as exc:
            self._connected = False
            logger.warning("FyersCandleFeed: candles fetch failed for %s: %s", symbol, exc)
            return []

    async def get_ltp(self, symbol: str) -> float:
        """Newest 1-minute close (minute-level realtime LTP)."""
        try:
            candles = await self.get_candles(symbol, timeframe="1m", count=1)
            if candles:
                return float(candles[-1].get("close", 0.0))
        except Exception as exc:
            logger.debug("FyersCandleFeed: LTP fetch failed for %s: %s", symbol, exc)
        return 0.0

    # ── BaseFeed plumbing ─────────────────────────────────────

    async def connect(self) -> Dict[str, Any]:
        return {"success": True, "message": "FyersCandleFeed ready (REST history API)"}

    async def disconnect(self) -> Dict[str, Any]:
        self._cache.clear()
        return {"success": True, "message": "FyersCandleFeed disconnected"}

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True, "count": len(symbols), "message": "REST feed — no subscription needed"}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True, "count": len(symbols), "message": "REST feed — no subscription needed"}

    def is_connected(self) -> bool:
        return self._connected

    def get_name(self) -> str:
        return "Fyers 1m Realtime"


async def build_fyers_candle_feed(repo_getter: Callable[..., Any]) -> Optional[FyersCandleFeed]:
    """Construct a FyersCandleFeed from STORED credentials if — and only if —
    a Fyers credential row exists with a still-valid access token.

    Returns None (caller keeps the Yahoo-only FeedManager) when:
      * no fyers credentials are stored,
      * the stored token is missing or expired,
      * anything unexpected happens (never crash app startup over a feed).
    """
    try:
        getter = repo_getter() 
        repo = await getter if asyncio.iscoroutine(getter) else getter
        try:
            cred = await repo.get_broker_credentials("fyers")
        finally:
            if hasattr(repo, "close"):
                try:
                    res = repo.close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass

        if cred is None or not getattr(cred, "encrypted_credentials", None):
            return None

        from utils.encryption import decrypt_credentials

        creds = decrypt_credentials(cred.encrypted_credentials) or {}
        access_token = str(creds.get("access_token") or "")
        app_id = str(creds.get("app_id") or creds.get("client_id") or "")
        if not access_token or not app_id:
            return None

        # Token expiry lives in extra JSON (written by the OAuth callback /
        # re-login flow). Absent expiry → treat as NOT valid (we cannot know).
        import json as _json

        extra: Dict[str, Any] = {}
        raw_extra = getattr(cred, "extra", None)
        if isinstance(raw_extra, dict):
            extra = raw_extra
        elif isinstance(raw_extra, str) and raw_extra:
            try:
                parsed = _json.loads(raw_extra)
                extra = parsed if isinstance(parsed, dict) else {}
            except (ValueError, TypeError):
                extra = {}

        expires_at = extra.get("token_expires_at")
        if not expires_at or float(expires_at) <= time.time():
            logger.info("FyersCandleFeed: stored token expired/unknown — staying on Yahoo")
            return None

        from brokers.fyers import FyersBroker

        def broker_factory() -> FyersBroker:
            return FyersBroker(app_id=app_id, access_token=access_token)

        feed = FyersCandleFeed(broker_factory=broker_factory)
        logger.info(
            "FyersCandleFeed ACTIVE as primary candle source (token valid ~%d min)",
            max(0, int(float(expires_at) - time.time())) // 60,
        )
        return feed
    except Exception as exc:
        logger.warning("FyersCandleFeed: could not build from stored credentials: %s", exc)
        return None


async def fetch_fyers_history_candles(
    repo_getter: Callable[..., Any],
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> List[Dict[str, Any]]:
    """Date-range candle fetch via stored Fyers credentials (P2-c).

    Used by the backtest runner as the PRIMARY history source when a valid
    Fyers token exists — Fyers serves months of 1-minute history while Yahoo
    caps 1m data at ~7 days. Returns [] (caller falls back to Yahoo) when
    credentials/token are unavailable or the fetch fails.

    timeframe: "1m" returns raw 1-minute bars; "5m"/"5min" aggregates the
    1-minute bars into 5-minute candles (same shape as the live feed).
    """
    feed = await build_fyers_candle_feed(repo_getter)
    if feed is None:
        return []
    try:
        from datetime import datetime as _dt

        def _norm_date(s: str) -> str:
            s = str(s or "").strip()
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
                try:
                    return _dt.strptime(s, fmt).strftime("%Y-%m-%d")
                except ValueError:
                    continue
            return s

        rng_from = _norm_date(start_date)
        rng_to = _norm_date(end_date)

        raw = await feed._broker.get_candles(
            to_fyers_symbol(symbol),
            resolution="1",
            range_from=rng_from,
            range_to=rng_to,
        )
        if not raw:
            return []

        one_min = [
            {
                "timestamp": c.get("timestamp"),
                "open": float(c.get("open", 0) or 0),
                "high": float(c.get("high", 0) or 0),
                "low": float(c.get("low", 0) or 0),
                "close": float(c.get("close", 0) or 0),
                "volume": float(c.get("volume", 0) or 0),
            }
            for c in raw
            if c.get("timestamp")
        ]
        one_min.sort(key=lambda c: float(c["timestamp"]))

        tf = (timeframe or "5m").lower()
        if tf in ("1m", "1min", "1"):
            return [
                {
                    "timestamp": _epoch_to_ist_iso(c["timestamp"]),
                    "open": round(c["open"], 2),
                    "high": round(c["high"], 2),
                    "low": round(c["low"], 2),
                    "close": round(c["close"], 2),
                    "volume": int(c["volume"]),
                }
                for c in one_min
            ]

        agg = aggregate_1m_to_5m(one_min)
        return [
            {
                "timestamp": c["timestamp"],
                "open": round(c["open"], 2),
                "high": round(c["high"], 2),
                "low": round(c["low"], 2),
                "close": round(c["close"], 2),
                "volume": int(c["volume"]),
            }
            for c in agg
        ]
    except Exception as exc:
        logger.warning("Fyers history fetch failed for %s: %s", symbol, exc)
        return []
