from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_vwap, calculate_rsi, calculate_sma


class MeanReversionForce(BaseStrategy):
    """MRF — Mean Reversion Force Strategy.

    Fades extreme price deviations (>2.0σ from intraday VWAP) back toward VWAP.
    Enforces strict regime locking (Longs in Bull/Sideways only, Shorts in Bear/Sideways only)
    and confirmed exhaustion triggers (RSI(5) bounce, engulfing, or 2.5σ touch).
    """

    name: str = "MRF"
    description: str = "Mean Reversion Force fading 2.0σ+ VWAP deviations back toward the mean."
    preferred_timeframes = ["5min"]
    best_regimes = ["Sideways", "Bull", "Bear"]
    worst_regimes = ["Volatile"]

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__(params=params)

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        if candles is None or len(candles) < 22:
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()

        # Time filter: 10:00 AM to 2:00 PM (14:00)
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < (10 * 60) or curr_min > (14 * 60):
                return None

        # Regime filter: Skip MRF in Volatile / HighVol
        if regime == "Volatile":
            return None

        close = df["close"]
        open_p = df["open"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        curr_close = float(close.iloc[-1])
        curr_open = float(open_p.iloc[-1])
        curr_high = float(high.iloc[-1])
        curr_low = float(low.iloc[-1])

        if curr_close <= 0 or curr_open <= 0:
            return None

        # 1. VWAP & Rolling Standard Deviation Bands
        vwap_series = calculate_vwap(high, low, close, vol)
        if vwap_series.isna().iloc[-1]:
            return None

        curr_vwap = float(vwap_series.iloc[-1])
        rolling_std = close.rolling(20, min_periods=10).std()
        std_val = float(rolling_std.iloc[-1]) if not rolling_std.isna().iloc[-1] else (curr_close * 0.005)

        upper_1_5 = curr_vwap + (1.5 * std_val)
        lower_1_5 = curr_vwap - (1.5 * std_val)
        upper_2_0 = curr_vwap + (2.0 * std_val)
        lower_2_0 = curr_vwap - (2.0 * std_val)
        upper_2_5 = curr_vwap + (2.5 * std_val)
        lower_2_5 = curr_vwap - (2.5 * std_val)

        # 2. RSI(5) Indicator
        rsi5_series = calculate_rsi(close, period=5)
        if rsi5_series.isna().iloc[-1]:
            return None

        curr_rsi5 = float(rsi5_series.iloc[-1])
        prev_rsi5 = float(rsi5_series.iloc[-2]) if len(rsi5_series) >= 2 else curr_rsi5

        direction = None

        # LONG SETUP:
        # Longs ONLY allowed in Bull or Sideways regimes (NEVER Bear counter-trend)
        if regime in ["Bull", "Sideways"]:
            # Price dropped to or below 2.0σ band
            touched_2_0 = (curr_low <= lower_2_0) or (float(low.iloc[-2]) <= lower_2_0)
            if touched_2_0:
                # Trigger conditions:
                # a) RSI(5) crosses back above 20
                rsi_bounce = (prev_rsi5 < 22.0 and curr_rsi5 >= 20.0 and curr_close > curr_open)
                # b) Bullish engulfing candle below 2.0σ
                prev_close = float(close.iloc[-2])
                prev_open = float(open_p.iloc[-2])
                is_engulfing = (curr_close > prev_open and curr_open < prev_close and curr_close > curr_open)
                # c) Touch of 2.5σ extreme band with bullish response
                touched_2_5 = (curr_low <= lower_2_5) and (curr_close > curr_open or curr_close > float(close.iloc[-2]))

                if rsi_bounce or is_engulfing or touched_2_5:
                    direction = "BUY"

        # SHORT SETUP:
        # Shorts ONLY allowed in Bear or Sideways regimes (NEVER Bull counter-trend)
        if regime in ["Bear", "Sideways"] and direction is None:
            touched_2_0 = (curr_high >= upper_2_0) or (float(high.iloc[-2]) >= upper_2_0)
            if touched_2_0:
                # Trigger conditions:
                # a) RSI(5) crosses back below 80
                rsi_bounce = (prev_rsi5 > 78.0 and curr_rsi5 <= 80.0 and curr_close < curr_open)
                # b) Bearish engulfing candle above 2.0σ
                prev_close = float(close.iloc[-2])
                prev_open = float(open_p.iloc[-2])
                is_engulfing = (curr_close < prev_open and curr_open > prev_close and curr_close < curr_open)
                # c) Touch of 2.5σ extreme band with bearish response
                touched_2_5 = (curr_high >= upper_2_5) and (curr_close < curr_open or curr_close < float(close.iloc[-2]))

                if rsi_bounce or is_engulfing or touched_2_5:
                    direction = "SELL"

        if direction is None:
            return None

        entry_price = curr_close

        # Stop Loss & Target Calculation:
        # SL beyond 2.5σ band ± 0.1% buffer
        # Maximum SL: 1.0% of entry price
        # Target = VWAP (mean)
        min_sl_dist = entry_price * 0.0025
        max_sl_dist = entry_price * 0.010

        if direction == "BUY":
            raw_sl = lower_2_5 - (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(entry_price - raw_sl, max_sl_dist))
            sl_price = round(entry_price - sl_dist, 2)
            target_price = round(curr_vwap, 2)
            if target_price <= entry_price:
                target_price = round(entry_price + (1.2 * sl_dist), 2)
        else:
            raw_sl = upper_2_5 + (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(raw_sl - entry_price, max_sl_dist))
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(curr_vwap, 2)
            if target_price >= entry_price:
                target_price = round(entry_price - (1.2 * sl_dist), 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 1.2

        confidence = 0.78
        if regime == "Sideways":
            confidence += 0.06  # MRF thrives best in Sideways/Range
        confidence = min(0.90, round(confidence, 2))

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
                "vwap": round(curr_vwap, 2),
                "rsi5": round(curr_rsi5, 2),
                "upper_2_0": round(upper_2_0, 2),
                "lower_2_0": round(lower_2_0, 2),
                "upper_2_5": round(upper_2_5, 2),
                "lower_2_5": round(lower_2_5, 2),
            },
        }
