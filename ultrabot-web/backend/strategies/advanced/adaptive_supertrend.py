import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_supertrend, calculate_atr


class AdaptiveSupertrendStrategy(BaseStrategy):
    """Supertrend with adaptive multiplier based on VIX.

    multiplier = 3 + (VIX - 15) / 10
    Low VIX (12) -> ~2.7, High VIX (25) -> ~4.0
    """

    name = "AdaptiveSupertrend"
    description = "Supertrend with VIX-adaptive ATR multiplier for varying volatility regimes."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bull", "Bear", "Sideways", "Volatile"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "st_period": 10,
        "base_multiplier": 3.0,
        "vix_reference": 15.0,
        "vix_sensitivity": 10.0,
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
        base_multiplier = self.params.get("base_multiplier", 3.0)
        vix_ref = self.params.get("vix_reference", 15.0)
        vix_sens = self.params.get("vix_sensitivity", 10.0)
        atr_period = self.params.get("atr_period", 14)
        target_atr_mult = self.params.get("target_atr_mult", 2.0)

        min_candles = st_period + atr_period + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Compute adaptive multiplier based on VIX
        effective_vix = vix if vix > 0 else vix_ref
        adaptive_multiplier = base_multiplier + (effective_vix - vix_ref) / vix_sens
        # Clamp to reasonable range [2.0, 5.0]
        adaptive_multiplier = max(2.0, min(5.0, adaptive_multiplier))

        # Calculate Supertrend with adaptive multiplier
        st_value, st_dir = calculate_supertrend(
            candles["high"], candles["low"], candles["close"],
            period=st_period, multiplier=adaptive_multiplier,
        )
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)

        current_dir = st_dir.iloc[-1]
        prev_dir = st_dir.iloc[-2]
        current_st = st_value.iloc[-1]
        current_close = candles["close"].iloc[-1]
        current_atr = atr.iloc[-1]

        if any(np.isnan(v) for v in [current_dir, prev_dir, current_st, current_atr]):
            return None

        if current_atr <= 0:
            return None

        # Detect flip
        direction = None
        if prev_dir == -1 and current_dir == 1:
            direction = "BUY"
        elif prev_dir == 1 and current_dir == -1:
            direction = "SELL"

        if direction is None:
            return None

        confidence = 0.4  # Base for flip

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

        # Candle body strength
        candle_body = abs(candles["close"].iloc[-1] - candles["open"].iloc[-1])
        if candle_body > current_atr * 0.5:
            confidence += 0.15

        # Regime alignment
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.15
        elif regime == "Volatile":
            # In volatile regime, the adaptive multiplier is doing more work, give small bonus
            confidence += 0.1

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
                "adaptive_multiplier": round(adaptive_multiplier, 3),
                "vix_used": effective_vix,
                "prev_direction": "bullish" if prev_dir == 1 else "bearish",
                "current_direction": "bullish" if current_dir == 1 else "bearish",
                "atr": round(current_atr, 2),
            },
        }
