import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_vwap


class VWAPReversionStrategy(BaseStrategy):
    """VWAP Reversion: enters when price deviates significantly from VWAP."""

    name = "VWAPReversion"
    description = "Sells when price > VWAP + 2σ, buys when price < VWAP - 2σ, targeting VWAP."
    preferred_timeframes = ["5min"]
    best_regimes = ["Sideways"]
    worst_regimes = ["Bull", "Bear"]
    params: Dict[str, Any] = {
        "std_mult_entry": 2.0,
        "std_mult_sl": 3.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        std_mult_entry = self.params.get("std_mult_entry", 2.0)
        std_mult_sl = self.params.get("std_mult_sl", 3.0)

        min_candles = 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Ensure datetime index for VWAP day grouping
        df = candles.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if 'timestamp' in df.columns:
                df.index = pd.to_datetime(df['timestamp'])
            else:
                return None

        # Calculate VWAP
        vwap = calculate_vwap(df["high"], df["low"], df["close"], df["volume"].astype(float))

        # Calculate rolling standard deviation of typical price from VWAP
        typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
        deviation = typical_price - vwap
        rolling_std = deviation.rolling(window=20, min_periods=10).std()

        current_close = df["close"].iloc[-1]
        current_vwap = vwap.iloc[-1]
        current_std = rolling_std.iloc[-1]
        current_deviation = deviation.iloc[-1]

        if any(np.isnan(v) for v in [current_vwap, current_std, current_deviation]):
            return None

        if current_std <= 0:
            return None

        confidence = 0.0
        direction = None
        entry_price = current_close

        # Overbought: price > VWAP + std_mult_entry * std
        upper_band = current_vwap + std_mult_entry * current_std
        lower_band = current_vwap - std_mult_entry * current_std

        if current_close > upper_band:
            direction = "SELL"
            sl_price = current_vwap + std_mult_sl * current_std
            target_price = current_vwap
            confidence += 0.45

        elif current_close < lower_band:
            direction = "BUY"
            sl_price = current_vwap - std_mult_sl * current_std
            target_price = current_vwap
            confidence += 0.45

        if direction is None:
            return None

        # Stronger deviation = higher confidence
        z_current = abs(current_deviation) / current_std
        if z_current > 2.5:
            confidence += 0.15
        if z_current > 3.0:
            confidence += 0.1

        # Regime alignment
        if regime == "Sideways":
            confidence += 0.15
        elif regime in self.worst_regimes:
            confidence -= 0.15

        # Volume check: higher volume on reversion candle supports the move
        if len(df) >= 20:
            avg_vol = df["volume"].iloc[-20:].astype(float).mean()
            if avg_vol > 0 and df["volume"].iloc[-1] > avg_vol * 1.2:
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
                "vwap": round(current_vwap, 2),
                "z_score": round(z_current, 3),
                "upper_band": round(upper_band, 2),
                "lower_band": round(lower_band, 2),
            },
        }
