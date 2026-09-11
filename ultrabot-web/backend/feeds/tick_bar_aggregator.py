"""In-Memory Real-Time Tick-to-Bar Aggregator.

Synthesizes rolling multi-timeframe OHLCV bars (10s, 1m, 5m, etc.)
directly from raw streaming ticks in RAM, completely bypassing REST API
calls and eliminating broker rate-limit consumption during market hours.
"""
import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from utils.candle_utils import candles_to_dataframe

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# Timeframe mapping to seconds
TIMEFRAME_SECONDS: Dict[str, int] = {
    "10s": 10,
    "15s": 15,
    "30s": 30,
    "1m": 60,
    "1min": 60,
    "3m": 180,
    "3min": 180,
    "5m": 300,
    "5min": 300,
    "15m": 900,
    "15min": 900,
    "60m": 3600,
    "60min": 3600,
    "1h": 3600,
    "1d": 86400,
    "1D": 86400,
}


def normalize_timeframe(tf: str) -> str:
    """Normalize timeframe string to canonical representation (e.g., '5min' -> '5m')."""
    tf_lower = (tf or "").strip().lower()
    if tf_lower in ("1m", "1min"):
        return "1m"
    elif tf_lower in ("3m", "3min"):
        return "3m"
    elif tf_lower in ("5m", "5min"):
        return "5m"
    elif tf_lower in ("15m", "15min"):
        return "15m"
    elif tf_lower in ("60m", "60min", "1h"):
        return "60m"
    elif tf_lower in ("1d", "daily"):
        return "1d"
    elif tf_lower in ("10s", "15s", "30s"):
        return tf_lower
    return tf_lower or "5m"


class FormingBar:
    """Represents an uncompleted candle currently absorbing ticks."""

    __slots__ = ("bucket_start", "open", "high", "low", "close", "volume", "ticks_count")

    def __init__(self, bucket_start: float, price: float, volume: int = 0):
        self.bucket_start = bucket_start
        self.open = float(price)
        self.high = float(price)
        self.low = float(price)
        self.close = float(price)
        self.volume = int(volume)
        self.ticks_count = 1

    def update(self, price: float, volume_increment: int = 0):
        p = float(price)
        if p > self.high:
            self.high = p
        if p < self.low:
            self.low = p
        self.close = p
        if volume_increment > 0:
            self.volume += int(volume_increment)
        self.ticks_count += 1

    def to_dict(self) -> Dict[str, Any]:
        dt = datetime.fromtimestamp(self.bucket_start, tz=IST)
        return {
            "timestamp": dt.isoformat(),
            "open": round(self.open, 2),
            "high": round(self.high, 2),
            "low": round(self.low, 2),
            "close": round(self.close, 2),
            "volume": int(self.volume),
        }


class TickBarAggregator:
    """High-performance in-memory tick-to-bar aggregator.

    Maintains fixed-capacity deques of completed bars plus forming bars
    across configured timeframes for each subscribed symbol.
    Thread-safe under GIL and internal mutex.
    """

    def __init__(
        self,
        timeframes: Optional[List[str]] = None,
        max_bars: int = 300,
    ):
        self.timeframes = [normalize_timeframe(tf) for tf in (timeframes or ["10s", "1m", "5m"])]
        self.max_bars = max_bars
        self._lock = threading.Lock()

        # symbol -> timeframe -> deque of completed bar dicts
        self._bars: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: {tf: deque(maxlen=self.max_bars) for tf in self.timeframes}
        )
        # symbol -> timeframe -> FormingBar
        self._forming: Dict[str, Dict[str, Optional[FormingBar]]] = defaultdict(
            lambda: {tf: None for tf in self.timeframes}
        )
        # symbol -> last known cumulative day volume
        self._last_day_volume: Dict[str, int] = {}
        # symbol -> last tick timestamp & price
        self._last_tick_time: Dict[str, float] = {}
        self._last_tick_price: Dict[str, float] = {}

        # Callbacks
        self._bar_completed_listeners: List[Callable[[str, str, Dict[str, Any]], None]] = []
        self._tick_listeners: List[Callable[[str, float, float], None]] = []

    def register_bar_completed_listener(self, fn: Callable[[str, str, Dict[str, Any]], None]) -> None:
        """Register callback for bar completion: fn(symbol, timeframe, bar_dict)."""
        with self._lock:
            if fn not in self._bar_completed_listeners:
                self._bar_completed_listeners.append(fn)

    def register_tick_listener(self, fn: Callable[[str, float, float], None]) -> None:
        """Register callback for tick arrival: fn(symbol, price, timestamp)."""
        with self._lock:
            if fn not in self._tick_listeners:
                self._tick_listeners.append(fn)

    def on_tick(
        self,
        symbol: str,
        price: float,
        volume: int = 0,
        timestamp: Optional[float] = None,
        cumulative_volume: bool = False,
    ) -> None:
        """Ingest a single tick and update all timeframes.

        Args:
            symbol: Trading symbol (e.g. 'SBIN', 'RELIANCE').
            price: Last traded price.
            volume: Tick volume or cumulative day volume.
            timestamp: Epoch timestamp in seconds. Defaults to time.time().
            cumulative_volume: True if 'volume' represents total day volume.
        """
        if price <= 0:
            return

        sym_upper = symbol.upper().strip()
        ts = float(timestamp if timestamp is not None and timestamp > 0 else time.time())
        price_flt = float(price)

        with self._lock:
            # Determine incremental volume for this tick
            vol_incr = 0
            if volume > 0:
                if cumulative_volume:
                    last_day_vol = self._last_day_volume.get(sym_upper, 0)
                    if last_day_vol > 0 and volume >= last_day_vol:
                        vol_incr = volume - last_day_vol
                    self._last_day_volume[sym_upper] = int(volume)
                else:
                    vol_incr = int(volume)

            self._last_tick_time[sym_upper] = ts
            self._last_tick_price[sym_upper] = price_flt

            completed_events = []

            for tf in self.timeframes:
                interval = TIMEFRAME_SECONDS.get(tf, 300)
                bucket_start = (int(ts) // interval) * interval

                forming = self._forming[sym_upper][tf]
                if forming is None:
                    # Initialize first forming bar
                    self._forming[sym_upper][tf] = FormingBar(bucket_start, price_flt, vol_incr)
                elif forming.bucket_start == bucket_start:
                    # Update current forming bar
                    forming.update(price_flt, vol_incr)
                else:
                    # New bucket started! Finalize previous bar
                    completed_bar_dict = forming.to_dict()
                    self._bars[sym_upper][tf].append(completed_bar_dict)
                    completed_events.append((sym_upper, tf, completed_bar_dict))

                    # Start new forming bar
                    self._forming[sym_upper][tf] = FormingBar(bucket_start, price_flt, vol_incr)

            tick_listeners = list(self._tick_listeners)
            bar_listeners = list(self._bar_completed_listeners)

        # Dispatch listeners outside lock to prevent deadlock
        for fn in tick_listeners:
            try:
                fn(sym_upper, price_flt, ts)
            except Exception as e:
                logger.debug("Error in tick listener: %s", e)

        for sym_evt, tf_evt, bar_evt in completed_events:
            for fn in bar_listeners:
                try:
                    fn(sym_evt, tf_evt, bar_evt)
                except Exception as e:
                    logger.debug("Error in bar completed listener: %s", e)

    def get_candles(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
        include_forming: bool = True,
    ) -> List[Dict[str, Any]]:
        """Retrieve rolling candles for a symbol.

        Args:
            symbol: Trading symbol.
            timeframe: Target timeframe ('10s', '1m', '5m', '15m', etc.).
            count: Number of recent candles to return.
            include_forming: Whether to include the current developing candle at index -1.

        Returns:
            List of candle dicts with keys: timestamp, open, high, low, close, volume.
        """
        sym_upper = symbol.upper().strip()
        tf = normalize_timeframe(timeframe)

        with self._lock:
            dq = self._bars.get(sym_upper, {}).get(tf)
            if not dq:
                res = []
            else:
                res = list(dq)

            if include_forming:
                forming = self._forming.get(sym_upper, {}).get(tf)
                if forming is not None:
                    res.append(forming.to_dict())

            if count and len(res) > count:
                res = res[-count:]

            return [dict(c) for c in res]

    def get_forming_candle(self, symbol: str, timeframe: str = "5m") -> Optional[Dict[str, Any]]:
        """Get the current developing bar for a symbol/timeframe, or None if none."""
        sym_upper = symbol.upper().strip()
        tf = normalize_timeframe(timeframe)
        with self._lock:
            forming = self._forming.get(sym_upper, {}).get(tf)
            return forming.to_dict() if forming is not None else None

    def get_candles_df(
        self,
        symbol: str,
        timeframe: str = "5m",
        count: int = 100,
        include_forming: bool = True,
    ) -> Any:
        """Return candles as a pandas DataFrame with normalized columns and DatetimeIndex."""
        candles = self.get_candles(symbol, timeframe, count=count, include_forming=include_forming)
        return candles_to_dataframe(candles)

    def seed_candles(
        self,
        symbol: str,
        timeframe: str,
        candles: List[Dict[str, Any]],
    ) -> int:
        """Seed historical candles (e.g. from backfill or broker history at startup).

        Args:
            symbol: Trading symbol.
            timeframe: Candle timeframe.
            candles: List of candle dicts.

        Returns:
            Count of successfully seeded candles.
        """
        if not candles:
            return 0

        sym_upper = symbol.upper().strip()
        tf = normalize_timeframe(timeframe)

        with self._lock:
            dq = self._bars[sym_upper][tf]
            added = 0
            for c in candles:
                if isinstance(c, dict) and "open" in c and "close" in c and "timestamp" in c:
                    bar = {
                        "timestamp": str(c["timestamp"]),
                        "open": round(float(c["open"]), 2),
                        "high": round(float(c.get("high", c["open"])), 2),
                        "low": round(float(c.get("low", c["open"])), 2),
                        "close": round(float(c["close"]), 2),
                        "volume": int(c.get("volume", 0) or 0),
                    }
                    dq.append(bar)
                    added += 1
            return added

    def get_latest_price(self, symbol: str) -> float:
        """Get the last seen tick price for a symbol."""
        sym_upper = symbol.upper().strip()
        with self._lock:
            return self._last_tick_price.get(sym_upper, 0.0)

    def get_symbol_status(self, symbol: str) -> Dict[str, Any]:
        """Get telemetry/health metrics for a symbol's aggregation."""
        sym_upper = symbol.upper().strip()
        with self._lock:
            last_ts = self._last_tick_time.get(sym_upper, 0.0)
            now = time.time()
            return {
                "symbol": sym_upper,
                "last_price": self._last_tick_price.get(sym_upper, 0.0),
                "last_tick_seconds_ago": round(now - last_ts, 2) if last_ts > 0 else None,
                "bars_count": {
                    tf: len(self._bars.get(sym_upper, {}).get(tf, []))
                    for tf in self.timeframes
                },
                "has_forming": {
                    tf: (self._forming.get(sym_upper, {}).get(tf) is not None)
                    for tf in self.timeframes
                },
            }

    def clear(self) -> None:
        """Reset all aggregations."""
        with self._lock:
            self._bars.clear()
            self._forming.clear()
            self._last_day_volume.clear()
            self._last_tick_time.clear()
            self._last_tick_price.clear()

    # Aliases
    add_bar_listener = register_bar_completed_listener
    add_tick_listener = register_tick_listener
    seed_history = seed_candles

