import numpy as np
import pandas as pd
from datetime import datetime, time
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_atr


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout: trades the first 15-30 min range on 5min candles."""

    name = "ORB_Classic"
    description = "Classic Opening Range Breakout (first 3-6 candles on 5min) with volume."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Sideways"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "range_candles": 6,
        "volume_mult": 1.2,
        "risk_reward_ratio": 2.0,
        "trade_start_time": "09:30",
        "trade_end_time": "11:00",
        "atr_period": 14,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        range_candles = self.params.get("range_candles", 6)
        volume_mult = self.params.get("volume_mult", 1.2)
        rr_ratio = self.params.get("risk_reward_ratio", 2.0)
        trade_start_str = self.params.get("trade_start_time", "09:30")
        trade_end_str = self.params.get("trade_end_time", "11:00")
        atr_period = self.params.get("atr_period", 14)

        min_candles = range_candles + 5
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Time gate: only trade between trade_start and trade_end
        current_ts = pd.to_datetime(candles.index[-1])
        if hasattr(current_ts, 'time'):
            current_time = current_ts.time()
        else:
            # If index is not datetime, try the 'timestamp' column
            ts_col = candles['timestamp'].iloc[-1] if 'timestamp' in candles.columns else None
            if ts_col is not None:
                current_time = pd.to_datetime(ts_col).time()
            else:
                return None

        start_time = datetime.strptime(trade_start_str, "%H:%M").time()
        end_time = datetime.strptime(trade_end_str, "%H:%M").time()
        if not (start_time <= current_time <= end_time):
            return None

        # Opening range: first N candles
        range_slice = candles.iloc[:range_candles]
        range_high = range_slice["high"].max()
        range_low = range_slice["low"].min()
        range_mid = (range_high + range_low) / 2.0

        # Average volume in opening range
        range_avg_vol = range_slice["volume"].astype(float).mean()

        # Current candle
        current_close = candles["close"].iloc[-1]
        current_volume = float(candles["volume"].iloc[-1])

        # ATR for SL sizing
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)
        current_atr = atr.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            current_atr = (range_high - range_low) * 0.5
            if current_atr <= 0:
                return None

        # Volume check
        volume_ok = range_avg_vol > 0 and current_volume >= range_avg_vol * volume_mult

        confidence = 0.0
        direction = None
        entry_price = current_close

        # Bullish breakout: close above range_high
        if current_close > range_high:
            direction = "BUY"
            sl_price = range_mid  # SL at midpoint of range
            risk = entry_price - sl_price
            if risk <= 0:
                sl_price = range_low  # Fallback to range low
                risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + rr_ratio * risk
            confidence += 0.45

        # Bearish breakout: close below range_low
        elif current_close < range_low:
            direction = "SELL"
            sl_price = range_mid
            risk = sl_price - entry_price
            if risk <= 0:
                sl_price = range_high
                risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - rr_ratio * risk
            confidence += 0.45

        if direction is None:
            return None

        if volume_ok:
            confidence += 0.3

        # Range quality: wider range = more confident
        range_width = range_high - range_low
        if range_width > current_atr * 0.5:
            confidence += 0.1

        # Regime bonus
        if regime in ["Bull", "Bear"]:
            confidence += 0.15

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
                "range_high": round(range_high, 2),
                "range_low": round(range_low, 2),
                "range_mid": round(range_mid, 2),
                "range_width": round(range_width, 2),
                "volume_ratio": round(current_volume / range_avg_vol, 2) if range_avg_vol > 0 else 0,
                "atr": round(current_atr, 2),
            },
        }
