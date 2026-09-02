from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_bollinger_bands, calculate_atr, calculate_sma


class MomentumBreakout(BaseStrategy):
    """MB — Momentum Breakout Strategy.

    Detects consolidation/coiling via Bollinger Bands bandwidth and inside-band persistence.
    Enters on a high-volume breakout candle aligned with multi-timeframe EMA trends.
    """

    name: str = "MB"
    description: str = "Momentum Breakout with Bollinger Band coiling, volume confirmation, and multi-timeframe trend alignment."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Volatile", "Sideways"]

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__(params=params)

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        if candles is None or len(candles) < 25:
            return None

        # Check required columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()
        
        # Time filter: 9:15 AM to 1:00 PM
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            entry_end_min = 13 * 60  # 1:00 PM
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min > entry_end_min:
                return None

        close = df["close"]
        open_p = df["open"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        curr_close = float(close.iloc[-1])
        curr_open = float(open_p.iloc[-1])
        curr_vol = float(vol.iloc[-1])

        if curr_close <= 0 or curr_open <= 0:
            return None

        # 1. Bollinger Bands (20, 2)
        upper, middle, lower = calculate_bollinger_bands(close, period=20, num_std=2.0)
        if upper.isna().iloc[-1] or middle.isna().iloc[-1] or lower.isna().iloc[-1]:
            return None

        bandwidth = (upper - lower) / middle
        curr_upper = float(upper.iloc[-1])
        curr_lower = float(lower.iloc[-1])
        curr_middle = float(middle.iloc[-1])
        curr_bandwidth = float(bandwidth.iloc[-1])

        # Range Preparation (over last 20 candles prior to breakout or including window):
        # - Price inside bands for at least 10 of last 20 candles
        inside_bands_count = sum(
            (close.iloc[-21:-1] >= lower.iloc[-21:-1]) & (close.iloc[-21:-1] <= upper.iloc[-21:-1])
        ) if len(close) >= 21 else sum((close >= lower) & (close <= upper))

        if inside_bands_count < 10:
            return None

        # - Bandwidth < 0.02 (or narrow coiling in recent 5 candles < 0.025)
        recent_bandwidth_min = float(bandwidth.iloc[-6:-1].min()) if len(bandwidth) >= 6 else curr_bandwidth
        if recent_bandwidth_min > 0.025 and curr_bandwidth > 0.025:
            return None

        # 2. Volume Confirmation
        vol_ma = calculate_sma(vol, period=20)
        avg_vol = float(vol_ma.iloc[-1]) if not vol_ma.isna().iloc[-1] else float(vol.mean())
        if avg_vol <= 0 or curr_vol < (1.5 * avg_vol):
            return None

        # 3. Breakout Candle Check
        body_pct = abs(curr_close - curr_open) / curr_open
        if body_pct < 0.003:  # Must be > 0.3% of price
            return None

        # 4. Trend Alignment
        # 5-min 20-EMA
        ema20_5m = calculate_ema(close, period=20)
        # 15-min 20-EMA approximation: on 5m candles, 15m 20-EMA corresponds to a 60-period EMA on 5m
        ema20_15m = calculate_ema(close, period=60) if len(close) >= 60 else calculate_ema(close, period=len(close))

        if ema20_5m.isna().iloc[-1] or len(ema20_5m) < 6:
            return None

        ema_5m_curr = float(ema20_5m.iloc[-1])
        ema_5m_5_ago = float(ema20_5m.iloc[-6])
        ema_15m_curr = float(ema20_15m.iloc[-1])

        # ATR calculation for Stop Loss
        atr_series = calculate_atr(high, low, close, period=14)
        atr = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else curr_close * 0.005

        direction = None
        # LONG check:
        # - Close above Upper Bollinger Band
        # - Bullish candle (close > open)
        # - 5-min 20-EMA is rising (ema_5m_curr > ema_5m_5_ago)
        # - Price is above 15-min 20-EMA (curr_close > ema_15m_curr)
        if (
            curr_close > curr_upper
            and curr_close > curr_open
            and ema_5m_curr > ema_5m_5_ago
            and curr_close > ema_15m_curr
        ):
            if regime != "Volatile":  # Skip MB in HighVol / Volatile per spec
                direction = "BUY"

        # SHORT check:
        # - Close below Lower Bollinger Band
        # - Bearish candle (close < open)
        # - 5-min 20-EMA is falling (ema_5m_curr < ema_5m_5_ago)
        # - Price is below 15-min 20-EMA (curr_close < ema_15m_curr)
        elif (
            curr_close < curr_lower
            and curr_close < curr_open
            and ema_5m_curr < ema_5m_5_ago
            and curr_close < ema_15m_curr
        ):
            if regime != "Volatile":
                direction = "SELL"

        if direction is None:
            return None

        entry_price = curr_close

        # Stop loss calculation:
        # SL = Entry - (1.5 * ATR) for BUY / Entry + (1.5 * ATR) for SELL
        # Minimum stop: 0.5% of price
        min_sl_dist = entry_price * 0.005
        raw_sl_dist = 1.5 * atr
        sl_dist = max(min_sl_dist, raw_sl_dist)
        # Cap max stop at 1.5%
        sl_dist = min(sl_dist, entry_price * 0.015)

        if direction == "BUY":
            sl_price = round(entry_price - sl_dist, 2)
            target_price = round(entry_price + (2.0 * sl_dist), 2)
        else:
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(entry_price - (2.0 * sl_dist), 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 2.0

        confidence = 0.78
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.07
        if curr_vol > 2.0 * avg_vol:
            confidence += 0.05
        confidence = min(0.92, round(confidence, 2))

        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "sl_price": sl_price,
            "target_price": target_price,
            "confidence": confidence,
            "strategy": self.name,
            "risk_reward": risk_reward,
            "extra_details": {
                "atr": round(atr, 2),
                "bandwidth": round(curr_bandwidth, 4),
                "ema20_5m": round(ema_5m_curr, 2),
                "ema20_15m": round(ema_15m_curr, 2),
                "upper_band": round(curr_upper, 2),
                "lower_band": round(curr_lower, 2),
            },
        }
