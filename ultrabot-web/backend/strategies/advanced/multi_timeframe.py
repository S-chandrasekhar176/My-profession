import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_atr


class MultiTimeframeStrategy(BaseStrategy):
    """Multi-Timeframe: requires 5min and 15min EMA alignment.

    Simulates multi-timeframe analysis by computing EMAs on different
    effective windows. When both the short-term (5min-like, EMA9/21) and
    medium-term (15min-like, EMA9/21 computed on every 3rd candle) trends
    align, a signal is generated.
    """

    name = "MultiTimeframe"
    description = "Enters when EMA trends align across simulated 5min and 15min timeframes."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Sideways"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "ema_fast": 9,
        "ema_slow": 21,
        "higher_tf_multiple": 3,  # 3x5min = 15min
        "atr_period": 14,
        "target_atr_mult": 2.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        ema_fast = self.params.get("ema_fast", 9)
        ema_slow = self.params.get("ema_slow", 21)
        higher_tf_multiple = self.params.get("higher_tf_multiple", 3)
        atr_period = self.params.get("atr_period", 14)
        target_atr_mult = self.params.get("target_atr_mult", 2.0)

        min_candles = ema_slow * higher_tf_multiple + 20
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # --- Lower timeframe (5min) EMAs ---
        ltf_ema_fast = calculate_ema(candles["close"], ema_fast)
        ltf_ema_slow = calculate_ema(candles["close"], ema_slow)

        # --- Higher timeframe EMAs via resampling ---
        # Resample to the higher timeframe. Scan candles are 5-minute bars, so
        # higher_tf_multiple=3 must yield 15-minute bars: 5 * 3 = "15min".
        # (The previous f"{higher_tf_multiple}min" produced 3-minute bars while
        # the code believed they were 15-minute — wrong HTF confirmation.)
        df = candles.copy()
        base_tf_minutes = 5
        if isinstance(df.index, pd.DatetimeIndex):
            resampled = df.resample(f"{base_tf_minutes * higher_tf_multiple}min").agg({
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }).dropna()
        else:
            # Fallback: take every Nth candle
            resampled = df.iloc[::higher_tf_multiple].copy()

        if len(resampled) < ema_slow + 5:
            return None

        htf_ema_fast = calculate_ema(resampled["close"], ema_fast)
        htf_ema_slow = calculate_ema(resampled["close"], ema_slow)

        # Current values
        ltf_fast_now = ltf_ema_fast.iloc[-1]
        ltf_slow_now = ltf_ema_slow.iloc[-1]
        htf_fast_now = htf_ema_fast.iloc[-1]
        htf_slow_now = htf_ema_slow.iloc[-1]

        if any(np.isnan(v) for v in [ltf_fast_now, ltf_slow_now, htf_fast_now, htf_slow_now]):
            return None

        # ATR on 5min
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)
        current_atr = atr.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            return None

        entry_price = candles["close"].iloc[-1]
        confidence = 0.0
        direction = None

        # Both timeframes bullish: fast > slow on both
        if ltf_fast_now > ltf_slow_now and htf_fast_now > htf_slow_now:
            direction = "BUY"
            confidence += 0.4
        # Both timeframes bearish
        elif ltf_fast_now < ltf_slow_now and htf_fast_now < htf_slow_now:
            direction = "SELL"
            confidence += 0.4

        if direction is None:
            return None

        # Higher timeframe trend strength
        htf_spread = abs(htf_fast_now - htf_slow_now) / htf_slow_now * 100
        if htf_spread > 0.5:
            confidence += 0.15
        if htf_spread > 1.0:
            confidence += 0.1

        # Volume confirmation
        if len(candles) >= 20:
            avg_vol = candles["volume"].iloc[-20:].astype(float).mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.3:
                confidence += 0.15

        # Regime bonus
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.1
        elif regime == "Sideways":
            confidence += 0.05

        confidence = min(confidence, 1.0)
        if confidence < 0.3:
            return None

        # SL and target using ATR
        if direction == "BUY":
            sl_price = entry_price - 1.0 * current_atr
            target_price = entry_price + target_atr_mult * current_atr
        else:
            sl_price = entry_price + 1.0 * current_atr
            target_price = entry_price - target_atr_mult * current_atr

        risk_val = abs(entry_price - sl_price)
        rr = abs(target_price - entry_price) / risk_val if risk_val > 0 else 0.0

        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "target_price": round(target_price, 2),
            "confidence": round(confidence, 2),
            "strategy": self.name,
            "risk_reward": round(rr, 2),
            "extra_details": {
                "ltf_ema_fast": round(ltf_fast_now, 2),
                "ltf_ema_slow": round(ltf_slow_now, 2),
                "htf_ema_fast": round(htf_fast_now, 2),
                "htf_ema_slow": round(htf_slow_now, 2),
                "htf_spread_pct": round(htf_spread, 3),
                "atr": round(current_atr, 2),
            },
        }
