import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_supertrend, calculate_atr


class SupertrendStrategy(BaseStrategy):
    """Supertrend flip strategy: enters on direction change of Supertrend."""

    name = "Supertrend"
    description = "Enters on Supertrend direction flip from sell->buy (BUY) or buy->sell (SELL)."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Sideways"]
    params: Dict[str, Any] = {
        "st_period": 10,
        "st_multiplier": 3.0,
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
        st_period = self.params.get("st_period", 10)
        st_multiplier = self.params.get("st_multiplier", 3.0)
        atr_period = self.params.get("atr_period", 14)
        target_atr_mult = self.params.get("target_atr_mult", 2.0)

        min_candles = st_period + atr_period + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Calculate Supertrend and ATR
        st_value, st_dir = calculate_supertrend(
            candles["high"], candles["low"], candles["close"],
            period=st_period, multiplier=st_multiplier,
        )
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)

        # Current values
        current_dir = st_dir.iloc[-1]
        prev_dir = st_dir.iloc[-2]
        current_st = st_value.iloc[-1]
        current_close = candles["close"].iloc[-1]
        current_atr = atr.iloc[-1]

        if np.isnan(current_dir) or np.isnan(prev_dir) or np.isnan(current_st) or np.isnan(current_atr):
            return None

        if current_atr <= 0:
            return None

        # Detect flip
        direction = None
        if prev_dir == -1 and current_dir == 1:
            direction = "BUY"  # Flip from sell to buy
        elif prev_dir == 1 and current_dir == -1:
            direction = "SELL"  # Flip from buy to sell

        if direction is None:
            return None

        confidence = 0.45  # Flip detected

        # SL at supertrend value
        sl_price = current_st
        entry_price = current_close

        if direction == "BUY":
            risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + target_atr_mult * current_atr
        else:
            risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - target_atr_mult * current_atr

        # Volume confirmation
        if len(candles) >= 20:
            avg_vol = candles["volume"].iloc[-20:].astype(float).mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.3:
                confidence += 0.2

        # Candle body confirmation: breakout candle should have strong body
        candle_body = abs(candles["close"].iloc[-1] - candles["open"].iloc[-1])
        if candle_body > current_atr * 0.5:
            confidence += 0.15

        # Regime alignment
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.2

        confidence = min(confidence, 1.0)
        if confidence < 0.3:
            return None

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
                "supertrend_value": round(current_st, 2),
                "prev_direction": "bullish" if prev_dir == 1 else "bearish",
                "current_direction": "bullish" if current_dir == 1 else "bearish",
                "atr": round(current_atr, 2),
            },
        }
