import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from utils.indicators import (
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_atr,
)
from utils.market_utils import get_last_candle_age_minutes

logger = logging.getLogger(__name__)

# Freshness guard (Phase 5): skip setups computed on candles whose newest bar
# is older than this many calendar days (delisted/suspended instruments can
# still serve OLD history through the feed).
_STALE_CANDLE_MAX_AGE_DAYS = 7.0

# Bollinger band squeeze threshold (lower band = tighter bands)
_BB_SQUEEZE_RATIO = 0.03  # Band width < 3% of price = squeeze

# Volume anomaly threshold
_VOLUME_ANOMALY_RATIO = 2.0  # 2x average volume

# Support/resistance proximity threshold
_SR_PROXIMITY_PCT = 2.0  # Within 2% of S/R level


class TechnicalScanner:
    """Scan for technical setups: breakouts, volume anomalies, S/R levels.

    Uses indicators from utils.indicators to compute technical metrics.
    """

    def __init__(
        self,
        bb_period: int = 20,
        bb_std: float = 2.0,
        rsi_period: int = 14,
        ema_fast: int = 20,
        ema_slow: int = 50,
        atr_period: int = 14,
    ):
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period

    async def scan(
        self,
        watchlist_symbols: List[str],
        feed: Any,
    ) -> List[Dict[str, Any]]:
        """Scan all watchlist symbols for technical setups.

        Args:
            watchlist_symbols: List of NSE symbols.
            feed: A feed object with get_candles(symbol, timeframe, count) method.

        Returns:
            List of setup dicts:
            [{symbol, setup_type, confidence, details}]
        """
        results = []
        for symbol in watchlist_symbols:
            try:
                candles = await feed.get_candles(symbol, timeframe="15m", count=100)
                if not candles or len(candles) < 30:
                    continue

                # ── Freshness guard (Phase 5) ──────────────────────────
                # Delisted/suspended symbols can still serve OLD history;
                # never rank technical setups computed on stale candles.
                age_minutes = get_last_candle_age_minutes(candles)
                if age_minutes is not None and age_minutes > _STALE_CANDLE_MAX_AGE_DAYS * 1440.0:
                    logger.info(
                        "Technical scan: skipping %s — newest candle is %.0f days old (delisted/suspended?)",
                        symbol, age_minutes / 1440.0,
                    )
                    continue

                setups = self._analyze_symbol(symbol, candles)
                results.extend(setups)
            except Exception as e:
                logger.warning("Technical scan error for %s: %s", symbol, e, exc_info=True)
                continue

        # Sort by confidence descending
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results

    def _analyze_symbol(self, symbol: str, candles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Analyze a single symbol's candles for setups."""
        df = self._candles_to_df(candles)
        if df is None or len(df) < 30:
            return []

        setups = []

        # Compute indicators
        upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(
            df["close"], self.bb_period, self.bb_std
        )
        rsi = calculate_rsi(df["close"], self.rsi_period)
        ema_fast = calculate_ema(df["close"], self.ema_fast)
        ema_slow = calculate_ema(df["close"], self.ema_slow)
        atr = calculate_atr(df["high"], df["low"], df["close"], self.atr_period)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        ltp = last["close"]
        last_valid_idx = len(df) - 1

        # 1. Bollinger Band Squeeze / Breakout
        setup = self._check_bb_breakout(
            symbol, ltp, upper_bb, lower_bb, middle_bb, last_valid_idx, df
        )
        if setup:
            setups.append(setup)

        # 2. Unusual Volume
        setup = self._check_volume_anomaly(symbol, df, ltp, last_valid_idx)
        if setup:
            setups.append(setup)

        # 3. Support / Resistance levels
        setup = self._check_support_resistance(
            symbol, ltp, df, last_valid_idx, atr, last_valid_idx
        )
        if setup:
            setups.append(setup)

        # 4. RSI Divergence / Extreme
        setup = self._check_rsi_setup(symbol, ltp, rsi, last_valid_idx)
        if setup:
            setups.append(setup)

        # 5. EMA Crossover
        if (not pd.isna(ema_fast.iloc[last_valid_idx]) and
                not pd.isna(ema_slow.iloc[last_valid_idx]) and
                not pd.isna(ema_fast.iloc[last_valid_idx - 1]) and
                not pd.isna(ema_slow.iloc[last_valid_idx - 1])):
            setup = self._check_ema_crossover(
                symbol, ltp, ema_fast, ema_slow, last_valid_idx
            )
            if setup:
                setups.append(setup)

        return setups

    def _check_bb_breakout(
        self,
        symbol: str,
        ltp: float,
        upper_bb: pd.Series,
        lower_bb: pd.Series,
        middle_bb: pd.Series,
        idx: int,
        df: pd.DataFrame,
    ) -> Optional[Dict[str, Any]]:
        """Check for Bollinger Band squeeze and breakout."""
        if idx < 0 or idx >= len(df) or idx >= len(upper_bb) or idx >= len(lower_bb) or idx >= len(middle_bb):
            return None
        if ltp <= 0 or pd.isna(upper_bb.iloc[idx]) or pd.isna(lower_bb.iloc[idx]) or pd.isna(middle_bb.iloc[idx]):
            return None

        mid_val = middle_bb.iloc[idx]
        bb_width = ((upper_bb.iloc[idx] - lower_bb.iloc[idx]) / mid_val) if mid_val > 0 else 0

        # Check for squeeze (narrow bands)
        is_squeeze = bb_width < _BB_SQUEEZE_RATIO

        # Check if price is near or breaking above upper band
        near_upper = ((upper_bb.iloc[idx] - ltp) / ltp < 0.005) if ltp > 0 else False
        above_upper = ltp > upper_bb.iloc[idx]
        near_lower = ((ltp - lower_bb.iloc[idx]) / ltp < 0.005) if ltp > 0 else False
        below_lower = ltp < lower_bb.iloc[idx]

        if is_squeeze and (near_upper or above_upper):
            confidence = 0.75 if above_upper else 0.55
            return {
                "symbol": symbol,
                "setup_type": "bb_squeeze_breakout_bullish",
                "confidence": round(confidence, 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "upper_bb": round(upper_bb.iloc[idx], 2),
                    "lower_bb": round(lower_bb.iloc[idx], 2),
                    "bb_width_pct": round(bb_width * 100, 2),
                    "above_upper": above_upper,
                },
            }

        if is_squeeze and (near_lower or below_lower):
            confidence = 0.75 if below_lower else 0.55
            return {
                "symbol": symbol,
                "setup_type": "bb_squeeze_breakout_bearish",
                "confidence": round(confidence, 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "upper_bb": round(upper_bb.iloc[idx], 2),
                    "lower_bb": round(lower_bb.iloc[idx], 2),
                    "bb_width_pct": round(bb_width * 100, 2),
                    "below_lower": below_lower,
                },
            }

        return None

    def _check_volume_anomaly(
        self,
        symbol: str,
        df: pd.DataFrame,
        ltp: float,
        idx: int,
    ) -> Optional[Dict[str, Any]]:
        """Check for unusual volume spikes."""
        if idx >= len(df) or idx < 1:
            return None

        lookback = min(20, idx)
        if lookback < 5:
            return None

        # HF-6 (v0.4.8): the plain mean baseline swallowed the
        # opening-auction spike bar (5-20x typical volume) whenever the
        # window spanned 09:15, so every rel-volume computed in the
        # 09:15-09:50 window read structurally low (live: 40 consecutive G15
        # rejections at 0.21x-0.69x vs the 1.00x requirement) and the gate
        # over-blocked the whole morning. Use a spike-TRIMMED baseline: drop
        # the top 20% of window bars before averaging. Robust to ANY volume
        # anomaly (not just the open) without requiring a historical
        # time-of-day volume profile.
        win_vals = [float(v) for v in df["volume"].iloc[idx - lookback:idx].tolist()]
        trim_n = max(1, int(len(win_vals) * 0.2))
        if len(win_vals) > trim_n + 1:
            kept = sorted(win_vals)[: len(win_vals) - trim_n]
            avg_vol = sum(kept) / len(kept)
        else:
            avg_vol = sum(win_vals) / len(win_vals)
        current_vol = df["volume"].iloc[idx]

        if avg_vol <= 0:
            return None

        ratio = current_vol / avg_vol

        if ratio >= _VOLUME_ANOMALY_RATIO:
            prev_close = df["close"].iloc[idx - 1]
            price_change = (((df["close"].iloc[idx] - prev_close) / prev_close) * 100) if prev_close > 0 else 0.0
            direction = "bullish" if price_change > 0 else "bearish"
            confidence = min(0.9, 0.4 + (ratio - _VOLUME_ANOMALY_RATIO) / 3.0)

            return {
                "symbol": symbol,
                "setup_type": f"unusual_volume_{direction}",
                "confidence": round(confidence, 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "volume_ratio": round(ratio, 2),
                    "current_volume": int(current_vol),
                    "avg_volume": int(avg_vol),
                    "price_change_pct": round(price_change, 2),
                },
            }

        return None

    def _check_support_resistance(
        self,
        symbol: str,
        ltp: float,
        df: pd.DataFrame,
        idx: int,
        atr: pd.Series,
        atr_idx: int,
    ) -> Optional[Dict[str, Any]]:
        """Detect price near support or resistance levels."""
        if idx >= len(df) or idx < 0 or atr_idx >= len(atr) or atr_idx < 0:
            return None
        if ltp <= 0 or pd.isna(atr.iloc[atr_idx]) or atr.iloc[atr_idx] <= 0:
            return None

        # Use recent highs/lows as S/R levels
        lookback = min(50, idx)
        if lookback < 5:
            return None
        recent_high = df["high"].iloc[idx - lookback:idx].max()
        recent_low = df["low"].iloc[idx - lookback:idx].min()

        if recent_high <= 0 or recent_low <= 0:
            return None

        dist_to_resistance = (((recent_high - ltp) / ltp) * 100) if ltp > 0 else 0.0
        dist_to_support = (((ltp - recent_low) / ltp) * 100) if ltp > 0 else 0.0

        if 0 < dist_to_resistance < _SR_PROXIMITY_PCT:
            confidence = 1.0 - (dist_to_resistance / _SR_PROXIMITY_PCT)
            return {
                "symbol": symbol,
                "setup_type": "near_resistance",
                "confidence": round(confidence, 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "resistance": round(recent_high, 2),
                    "distance_pct": round(dist_to_resistance, 2),
                    "atr": round(atr.iloc[atr_idx], 2),
                },
            }

        if 0 < dist_to_support < _SR_PROXIMITY_PCT:
            confidence = 1.0 - (dist_to_support / _SR_PROXIMITY_PCT)
            return {
                "symbol": symbol,
                "setup_type": "near_support",
                "confidence": round(confidence, 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "support": round(recent_low, 2),
                    "distance_pct": round(dist_to_support, 2),
                    "atr": round(atr.iloc[atr_idx], 2),
                },
            }

        return None

    def _check_rsi_setup(
        self,
        symbol: str,
        ltp: float,
        rsi: pd.Series,
        idx: int,
    ) -> Optional[Dict[str, Any]]:
        """Check for RSI extremes and divergences."""
        if pd.isna(rsi.iloc[idx]) or pd.isna(rsi.iloc[idx - 1]):
            return None

        rsi_val = rsi.iloc[idx]

        if rsi_val < 25:
            return {
                "symbol": symbol,
                "setup_type": "rsi_deep_oversold",
                "confidence": round(min(0.85, 0.5 + (30 - rsi_val) / 20), 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "rsi": round(rsi_val, 1),
                },
            }

        if rsi_val > 75:
            return {
                "symbol": symbol,
                "setup_type": "rsi_deep_overbought",
                "confidence": round(min(0.85, 0.5 + (rsi_val - 70) / 20), 2),
                "details": {
                    "ltp": round(ltp, 2),
                    "rsi": round(rsi_val, 1),
                },
            }

        return None

    @staticmethod
    def _check_ema_crossover(
        symbol: str,
        ltp: float,
        ema_fast: pd.Series,
        ema_slow: pd.Series,
        idx: int,
    ) -> Optional[Dict[str, Any]]:
        """Check for EMA fast/slow crossover."""
        curr_fast = ema_fast.iloc[idx]
        curr_slow = ema_slow.iloc[idx]
        prev_fast = ema_fast.iloc[idx - 1]
        prev_slow = ema_slow.iloc[idx - 1]

        # Bullish crossover: fast crosses above slow
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return {
                "symbol": symbol,
                "setup_type": "ema_bullish_crossover",
                "confidence": 0.65,
                "details": {
                    "ltp": round(ltp, 2),
                    "ema_fast": round(curr_fast, 2),
                    "ema_slow": round(curr_slow, 2),
                },
            }

        # Bearish crossover: fast crosses below slow
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return {
                "symbol": symbol,
                "setup_type": "ema_bearish_crossover",
                "confidence": 0.65,
                "details": {
                    "ltp": round(ltp, 2),
                    "ema_fast": round(curr_fast, 2),
                    "ema_slow": round(curr_slow, 2),
                },
            }

        return None

    @staticmethod
    def _candles_to_df(candles: List[Dict[str, Any]]) -> Optional[pd.DataFrame]:
        """Convert candle list to pandas DataFrame."""
        try:
            df = pd.DataFrame(candles)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                df = df.set_index("timestamp")
            for col in ["open", "high", "low", "close"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            df = df.dropna(subset=["open", "high", "low", "close"])
            return df
        except Exception as e:
            logger.warning("Failed to convert candles to DataFrame: %s", e, exc_info=True)
            return None
