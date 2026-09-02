import numpy as np
import pandas as pd
from typing import Dict, Optional, Any, List

from ..base import BaseStrategy
from utils.indicators import calculate_sma, calculate_atr


class BreakoutStrategy(BaseStrategy):
    """Breakout strategy: price breaks above resistance or below support with volume confirmation."""

    name = "Breakout"
    description = "Identifies price breakouts above 20-period high or below 20-period low with volume surge."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Sideways"]
    params: Dict[str, Any] = {
        "lookback": 20,
        "buffer_pct": 0.5,
        "volume_mult": 1.5,
        "risk_reward_ratio": 2.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        lookback = self.params.get("lookback", 20)
        buffer_pct = self.params.get("buffer_pct", 0.5) / 100.0
        volume_mult = self.params.get("volume_mult", 1.5)
        rr_ratio = self.params.get("risk_reward_ratio", 2.0)

        min_candles = lookback + 5
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Calculate indicators
        sma_high = calculate_sma(candles["high"], lookback)
        sma_low = calculate_sma(candles["low"], lookback)
        atr = calculate_atr(candles["high"], candles["low"], candles["close"])
        avg_volume = calculate_sma(candles["volume"].astype(float), lookback)

        # Current values
        current_close = candles["close"].iloc[-1]
        current_volume = float(candles["volume"].iloc[-1])
        current_atr = atr.iloc[-1]
        prev_sma_high = sma_high.iloc[-2]
        prev_sma_low = sma_low.iloc[-2]
        prev_avg_vol = avg_volume.iloc[-2]

        if any(np.isnan(v) for v in [prev_sma_high, prev_sma_low, prev_avg_vol, current_atr]):
            return None

        if current_atr <= 0:
            return None

        # Recent high/low over lookback period
        recent_highs = candles["high"].iloc[-(lookback + 1):-1]
        recent_lows = candles["low"].iloc[-(lookback + 1):-1]
        resistance = recent_highs.max()
        support = recent_lows.min()

        confidence = 0.0
        direction = None
        entry_price = current_close
        sl_price = None
        target_price = None

        # Bullish breakout: close > resistance + buffer
        if current_close > resistance * (1 + buffer_pct):
            direction = "BUY"
            sl_price = resistance
            risk = entry_price - sl_price
            target_price = entry_price + rr_ratio * risk
            confidence += 0.4  # Breakout condition met

        # Bearish breakout: close < support - buffer
        elif current_close < support * (1 - buffer_pct):
            direction = "SELL"
            sl_price = support
            risk = sl_price - entry_price
            target_price = entry_price - rr_ratio * risk
            confidence += 0.4  # Breakout condition met

        if direction is None:
            return None

        # Volume confirmation
        if prev_avg_vol > 0 and current_volume >= prev_avg_vol * volume_mult:
            confidence += 0.35

        # ATR-based filter: ensure the breakout candle body is significant
        candle_body = abs(candles["close"].iloc[-1] - candles["open"].iloc[-1])
        if candle_body > current_atr * 0.5:
            confidence += 0.15

        # Trend alignment bonus
        if direction == "BUY" and regime == "Bull":
            confidence += 0.1
        elif direction == "SELL" and regime == "Bear":
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
                "resistance": round(resistance, 2),
                "support": round(support, 2),
                "current_volume": current_volume,
                "avg_volume": round(prev_avg_vol, 0),
                "volume_ratio": round(current_volume / prev_avg_vol, 2) if prev_avg_vol > 0 else 0,
                "atr": round(current_atr, 2),
            },
        }
