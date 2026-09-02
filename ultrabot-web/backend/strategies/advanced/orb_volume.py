import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_atr


class ORBVolumeStrategy(BaseStrategy):
    """Opening Range Breakout with strict volume requirement (2x+ opening range average)."""

    name = "ORBVolume"
    description = "ORB with 2x+ volume requirement for higher conviction breakouts."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Volatile"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "range_candles": 6,
        "volume_mult": 2.0,
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
        volume_mult = self.params.get("volume_mult", 2.0)
        rr_ratio = self.params.get("risk_reward_ratio", 2.0)
        trade_start_str = self.params.get("trade_start_time", "09:30")
        trade_end_str = self.params.get("trade_end_time", "11:00")
        atr_period = self.params.get("atr_period", 14)

        min_candles = range_candles + 5
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Time gate
        current_ts = candles.index[-1]
        if isinstance(current_ts, pd.Timestamp):
            current_time = current_ts.time()
        elif 'timestamp' in candles.columns:
            current_time = pd.to_datetime(candles['timestamp'].iloc[-1]).time()
        else:
            return None

        start_time = datetime.strptime(trade_start_str, "%H:%M").time()
        end_time = datetime.strptime(trade_end_str, "%H:%M").time()
        if not (start_time <= current_time <= end_time):
            return None

        # Opening range
        range_slice = candles.iloc[:range_candles]
        range_high = range_slice["high"].max()
        range_low = range_slice["low"].min()
        range_mid = (range_high + range_low) / 2.0
        range_avg_vol = range_slice["volume"].astype(float).mean()

        # Current candle
        current_close = candles["close"].iloc[-1]
        current_volume = float(candles["volume"].iloc[-1])

        # ATR
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)
        current_atr = atr.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            current_atr = (range_high - range_low) * 0.5
            if current_atr <= 0:
                return None

        # Strict volume check: 2x+ opening range average
        volume_ok = range_avg_vol > 0 and current_volume >= range_avg_vol * volume_mult
        if not volume_ok:
            return None

        confidence = 0.0
        direction = None
        entry_price = current_close

        # Bullish breakout
        if current_close > range_high:
            direction = "BUY"
            sl_price = range_low
            risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + rr_ratio * risk
            confidence += 0.4

        # Bearish breakout
        elif current_close < range_low:
            direction = "SELL"
            sl_price = range_high
            risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - rr_ratio * risk
            confidence += 0.4

        if direction is None:
            return None

        # Volume confirmed (already checked, but add to confidence)
        confidence += 0.35

        # Volume strength bonus
        vol_ratio = current_volume / range_avg_vol if range_avg_vol > 0 else 0
        if vol_ratio > 3.0:
            confidence += 0.1

        # Range quality
        range_width = range_high - range_low
        if range_width > current_atr * 0.5:
            confidence += 0.1

        # Regime bonus
        if regime in ["Bull", "Bear", "Volatile"]:
            confidence += 0.05

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
                "volume_ratio": round(vol_ratio, 2),
                "volume_mult_required": volume_mult,
                "atr": round(current_atr, 2),
            },
        }