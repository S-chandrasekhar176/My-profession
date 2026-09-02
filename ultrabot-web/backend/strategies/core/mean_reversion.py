import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_sma, calculate_z_score


class MeanReversionStrategy(BaseStrategy):
    """Mean reversion strategy: enters when price deviates significantly from SMA."""

    name = "MeanReversion"
    description = "Buys/sells when z-score of price vs SMA20 exceeds ±2.0, targeting mean reversion."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Sideways"]
    worst_regimes = ["Bull", "Bear"]
    params: Dict[str, Any] = {
        "sma_period": 20,
        "z_entry": 2.0,
        "sl_mult": 1.5,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        sma_period = self.params.get("sma_period", 20)
        z_entry = self.params.get("z_entry", 2.0)
        sl_mult = self.params.get("sl_mult", 1.5)

        min_candles = sma_period + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        sma = calculate_sma(candles["close"], sma_period)
        z = calculate_z_score(candles["close"], sma_period)

        # Also compute rolling std for SL/target
        rolling_std = candles["close"].rolling(window=sma_period, min_periods=sma_period).std()

        current_close = candles["close"].iloc[-1]
        current_z = z.iloc[-1]
        current_sma = sma.iloc[-1]
        current_std = rolling_std.iloc[-1]

        if any(np.isnan(v) for v in [current_z, current_sma, current_std]):
            return None

        if current_std <= 0:
            return None

        confidence = 0.0
        direction = None
        entry_price = current_close

        # Overbought: z > z_entry -> SELL (expect reversion down)
        if current_z > z_entry:
            direction = "SELL"
            sl_price = entry_price + sl_mult * current_std
            target_price = current_sma
            confidence += 0.5

        # Oversold: z < -z_entry -> BUY (expect reversion up)
        elif current_z < -z_entry:
            direction = "BUY"
            sl_price = entry_price - sl_mult * current_std
            target_price = current_sma
            confidence += 0.5

        if direction is None:
            return None

        # Stronger z-score = higher confidence
        if abs(current_z) > 2.5:
            confidence += 0.15
        if abs(current_z) > 3.0:
            confidence += 0.1

        # Regime alignment
        if regime == "Sideways":
            confidence += 0.15
        elif regime in self.worst_regimes:
            confidence -= 0.15

        # Volume confirmation: check if current volume > previous average
        if len(candles) >= sma_period:
            avg_vol = candles["volume"].iloc[-sma_period:].mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.2:
                confidence += 0.1

        confidence = max(0.0, min(confidence, 1.0))
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
                "z_score": round(float(current_z), 3),
                "sma": round(current_sma, 2),
                "std_dev": round(current_std, 3),
                "z_entry_threshold": z_entry,
            },
        }
