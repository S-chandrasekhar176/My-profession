from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_sma, calculate_atr


class SignalIgnitionCandle(BaseStrategy):
    """SIC — Signal Ignition Candle Strategy.

    Detects statistically abnormal directional ignition candles (body > 2x average, body > 70% range,
    opposite wick < 20% range) and enters on the confirmed breakout of the ignition candle's high/low
    within 1 to 3 candles.
    """

    name: str = "SIC"
    description: str = "Signal Ignition Candle capturing explosive directional moves upon breakout of ignition candle."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Sideways", "Volatile"]
    worst_regimes = []

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

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()

        # Time filter: 9:20 AM to 2:00 PM
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < (9 * 60 + 20) or curr_min > (14 * 60):
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

        # 1. 20-period average body and volume
        bodies = (close - open_p).abs()
        ranges = high - low
        avg_body_series = calculate_sma(bodies, period=20)
        avg_vol_series = calculate_sma(vol, period=20)

        if avg_body_series.isna().iloc[-1]:
            return None

        # Look for ignition candles formed 1, 2, or 3 candles ago (indices -2, -3, -4)
        # Check from newest to oldest
        direction = None
        ignition_candle = None
        candles_since = 0

        for lookback in [1, 2, 3]:
            idx = -(lookback + 1)  # -2, -3, -4
            if abs(idx) > len(df):
                continue

            ign_open = float(open_p.iloc[idx])
            ign_close = float(close.iloc[idx])
            ign_high = float(high.iloc[idx])
            ign_low = float(low.iloc[idx])
            ign_vol = float(vol.iloc[idx])
            ign_body = abs(ign_close - ign_open)
            ign_range = ign_high - ign_low

            avg_b = float(avg_body_series.iloc[idx]) if not np.isnan(avg_body_series.iloc[idx]) else float(bodies.mean())
            avg_v = float(avg_vol_series.iloc[idx]) if not np.isnan(avg_vol_series.iloc[idx]) else float(vol.mean())

            if avg_b <= 0 or ign_range <= 0:
                continue

            # Criteria 1: Body > 2.0x average body
            if ign_body < (2.0 * avg_b):
                continue

            # Criteria 2: Body / Range > 0.70
            if (ign_body / ign_range) < 0.70:
                continue

            # Criteria 3: Skip if ignition candle was the first candle of the day (9:15 AM)
            if isinstance(df.index, pd.DatetimeIndex):
                ign_time = df.index[idx].time()
                if ign_time.hour == 9 and ign_time.minute == 15:
                    continue

            # Criteria 4: Direction & Wick Constraints
            # Bullish ignition:
            if ign_close > ign_open:
                lower_wick = ign_open - ign_low
                if (lower_wick / ign_range) < 0.20:
                    # Check if current candle triggers breakout above ignition high
                    # and price hasn't fallen below ignition low
                    intermediate_lows = df["low"].iloc[idx + 1 :]
                    if not (intermediate_lows < ign_low).any():
                        if curr_close > ign_high or (curr_high > ign_high and curr_close > curr_open):
                            direction = "BUY"
                            ignition_candle = {
                                "high": ign_high,
                                "low": ign_low,
                                "body": ign_body,
                                "close": ign_close,
                                "open": ign_open,
                                "vol": ign_vol,
                                "avg_vol": avg_v,
                            }
                            candles_since = lookback
                            break

            # Bearish ignition:
            elif ign_close < ign_open:
                upper_wick = ign_high - ign_open
                if (upper_wick / ign_range) < 0.20:
                    intermediate_highs = df["high"].iloc[idx + 1 :]
                    if not (intermediate_highs > ign_high).any():
                        if curr_close < ign_low or (curr_low < ign_low and curr_close < curr_open):
                            direction = "SELL"
                            ignition_candle = {
                                "high": ign_high,
                                "low": ign_low,
                                "body": ign_body,
                                "close": ign_close,
                                "open": ign_open,
                                "vol": ign_vol,
                                "avg_vol": avg_v,
                            }
                            candles_since = lookback
                            break

        if direction is None or ignition_candle is None:
            return None

        entry_price = curr_close
        ign_low = ignition_candle["low"]
        ign_high = ignition_candle["high"]
        ign_body = ignition_candle["body"]

        # Stop Loss Calculation:
        # SL = Ignition Low - 0.1% for longs / Ignition High + 0.1% for shorts
        # Min SL: 0.25%, Max SL: 1.2%
        min_sl_dist = entry_price * 0.0025
        max_sl_dist = entry_price * 0.012

        if direction == "BUY":
            raw_sl = ign_low - (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(entry_price - raw_sl, max_sl_dist))
            sl_price = round(entry_price - sl_dist, 2)
            target_price = round(entry_price + (1.9 * sl_dist), 2)
        else:
            raw_sl = ign_high + (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(raw_sl - entry_price, max_sl_dist))
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(entry_price - (1.9 * sl_dist), 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 1.9

        confidence = 0.80
        if ignition_candle["vol"] > 1.2 * ignition_candle["avg_vol"]:
            confidence += 0.05
        if candles_since == 1:
            confidence += 0.04  # Immediate follow-through is strongest
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
                "ignition_high": round(ign_high, 2),
                "ignition_low": round(ign_low, 2),
                "ignition_body": round(ign_body, 2),
                "candles_since_ignition": candles_since,
            },
        }
