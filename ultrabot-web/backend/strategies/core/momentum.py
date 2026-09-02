import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_rate_of_change, calculate_atr, calculate_ema


class MomentumStrategy(BaseStrategy):
    """Momentum strategy: rides strong directional moves confirmed by volume and trend."""

    name = "Momentum"
    description = "Enters on strong ROC with volume surge and EMA trend alignment."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Sideways"]
    params: Dict[str, Any] = {
        "roc_period": 10,
        "roc_threshold": 2.0,
        "volume_mult": 2.0,
        "ema_fast": 9,
        "ema_slow": 21,
        "atr_period": 14,
        "sl_atr_mult": 1.0,
        "target_atr_mult": 2.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        roc_period = self.params.get("roc_period", 10)
        roc_threshold = self.params.get("roc_threshold", 2.0)
        volume_mult = self.params.get("volume_mult", 2.0)
        ema_fast_period = self.params.get("ema_fast", 9)
        ema_slow_period = self.params.get("ema_slow", 21)
        atr_period = self.params.get("atr_period", 14)
        sl_atr_mult = self.params.get("sl_atr_mult", 1.0)
        target_atr_mult = self.params.get("target_atr_mult", 2.0)

        min_candles = max(roc_period, ema_slow_period, atr_period) + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Calculate indicators
        roc = calculate_rate_of_change(candles["close"], roc_period)
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)
        ema_fast = calculate_ema(candles["close"], ema_fast_period)
        ema_slow = calculate_ema(candles["close"], ema_slow_period)
        avg_volume = candles["volume"].astype(float).rolling(window=20, min_periods=20).mean()

        current_close = candles["close"].iloc[-1]
        current_roc = roc.iloc[-1]
        current_atr = atr.iloc[-1]
        current_ema_fast = ema_fast.iloc[-1]
        current_ema_slow = ema_slow.iloc[-1]
        current_volume = float(candles["volume"].iloc[-1])
        current_avg_vol = avg_volume.iloc[-1]

        if any(np.isnan(v) for v in [current_roc, current_atr, current_ema_fast, current_ema_slow, current_avg_vol]):
            return None

        if current_atr <= 0:
            return None

        confidence = 0.0
        direction = None
        entry_price = current_close

        # Bullish momentum: ROC > threshold
        if current_roc > roc_threshold:
            direction = "BUY"
            confidence += 0.35

        # Bearish momentum: ROC < -threshold
        elif current_roc < -roc_threshold:
            direction = "SELL"
            confidence += 0.35

        if direction is None:
            return None

        # EMA trend alignment
        if direction == "BUY" and current_ema_fast > current_ema_slow:
            confidence += 0.25
        elif direction == "SELL" and current_ema_fast < current_ema_slow:
            confidence += 0.25

        # Volume confirmation
        if current_avg_vol > 0 and current_volume >= current_avg_vol * volume_mult:
            confidence += 0.25

        # Regime alignment
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.15

        confidence = min(confidence, 1.0)
        if confidence < 0.35:
            return None

        # Calculate SL and target using ATR
        if direction == "BUY":
            sl_price = entry_price - sl_atr_mult * current_atr
            target_price = entry_price + target_atr_mult * current_atr
        else:
            sl_price = entry_price + sl_atr_mult * current_atr
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
                "roc": round(float(current_roc), 3),
                "roc_threshold": roc_threshold,
                "ema_fast": round(current_ema_fast, 2),
                "ema_slow": round(current_ema_slow, 2),
                "volume_ratio": round(current_volume / current_avg_vol, 2) if current_avg_vol > 0 else 0,
                "atr": round(current_atr, 2),
            },
        }
