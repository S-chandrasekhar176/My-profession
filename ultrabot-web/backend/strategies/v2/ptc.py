from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_rsi, calculate_sma


class PullbackTrendContinuation(BaseStrategy):
    """PTC — Pullback Trend Continuation Strategy.

    Waits for a healthy pullback (counter-trend candles) to 20-EMA or 50-EMA in an
    established higher-timeframe trend, then enters upon a confirmed reversal candle breakout.
    """

    name: str = "PTC"
    description: str = "Pullback Trend Continuation entering on EMA pullbacks with reversal candle and RSI confirmation."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Sideways", "Volatile"]

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

        # Time filter: Exit by 2:30 PM (no new entries after 14:30)
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            if (curr_time.hour * 60 + curr_time.minute) > (14 * 60 + 30):
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

        # Regime filter: PTC only operates in Bull or Bear regimes
        if regime not in ["Bull", "Bear"]:
            return None

        # 1. Indicators
        ema20 = calculate_ema(close, period=20)
        ema50 = close.ewm(span=50, adjust=False, min_periods=min(len(close), 20)).mean()
        htf_span = min(60, len(close))
        ema_htf = close.ewm(span=htf_span, adjust=False, min_periods=5).mean()
        rsi14 = calculate_rsi(close, period=14)
        vol_sma = calculate_sma(vol, period=20)

        if ema20.isna().iloc[-1] or rsi14.isna().iloc[-1]:
            return None

        # 2. Trend Establishment Check
        htf_rising = (ema_htf.iloc[-1] > ema_htf.iloc[-4]) and (ema20.iloc[-1] > ema20.iloc[-4])
        htf_falling = (ema_htf.iloc[-1] < ema_htf.iloc[-4]) and (ema20.iloc[-1] < ema20.iloc[-4])

        avg_vol = float(vol_sma.iloc[-1]) if not vol_sma.isna().iloc[-1] else float(vol.mean())

        direction = None
        pullback_extreme_low = None
        pullback_extreme_high = None
        pullback_type = "20-EMA"

        # --- LONG SETUP ---
        if htf_rising and regime == "Bull":
            recent_candles = df.iloc[-8:-1]
            red_count = sum(recent_candles["close"] < recent_candles["open"])
            
            # Invalidation: If price closed below 50-EMA, cancel
            if any(recent_candles["close"] < ema50.iloc[-8:-1]):
                return None

            touches_20ema = any(
                (abs(recent_candles["low"] - ema20.iloc[-8:-1]) / ema20.iloc[-8:-1] <= 0.005)
                | (recent_candles["low"] <= ema20.iloc[-8:-1])
            )
            touches_50ema = any(recent_candles["low"] <= ema50.iloc[-8:-1])

            if red_count >= 2 and (touches_20ema or touches_50ema):
                if touches_50ema:
                    pullback_type = "50-EMA"

                for rev_idx in [-2, -1]:
                    r_bar = df.iloc[rev_idx]
                    r_open = float(r_bar["open"])
                    r_close = float(r_bar["close"])
                    r_high = float(r_bar["high"])
                    r_low = float(r_bar["low"])
                    r_body = abs(r_close - r_open)
                    r_range = r_high - r_low
                    r_rsi = float(rsi14.iloc[rev_idx])
                    r_vol = float(vol.iloc[rev_idx])

                    if r_range <= 0:
                        continue

                    # Bullish reversal criteria:
                    prev_bar = df.iloc[rev_idx - 1]
                    is_engulfing = (
                        r_close > float(prev_bar["open"])
                        and r_open < float(prev_bar["close"])
                        and r_close > r_open
                    )
                    lower_wick = min(r_open, r_close) - r_low
                    is_hammer = (lower_wick > 1.2 * r_body) and ((r_close - r_low) > 0.4 * r_range)
                    is_strong_close = (r_close > r_open) and ((r_close - r_low) > 0.5 * r_range)

                    is_valid_reversal = is_engulfing or is_hammer or is_strong_close
                    rsi_ok = 30.0 <= r_rsi <= 75.0
                    vol_ok = r_vol >= 0.70 * avg_vol

                    if is_valid_reversal and rsi_ok and vol_ok:
                        if rev_idx == -2:
                            if curr_close > r_high or (curr_high > r_high and curr_close > curr_open):
                                direction = "BUY"
                                pullback_extreme_low = float(df.iloc[-8:]["low"].min())
                                break
                        elif rev_idx == -1:
                            if is_strong_close and curr_close > float(df.iloc[-2]["high"]):
                                direction = "BUY"
                                pullback_extreme_low = float(df.iloc[-8:]["low"].min())
                                break

        # --- SHORT SETUP ---
        elif htf_falling and regime == "Bear":
            recent_candles = df.iloc[-8:-1]
            green_count = sum(recent_candles["close"] > recent_candles["open"])

            # Invalidation: If price closed above 50-EMA, cancel
            if any(recent_candles["close"] > ema50.iloc[-8:-1]):
                return None

            touches_20ema = any(
                (abs(recent_candles["high"] - ema20.iloc[-8:-1]) / ema20.iloc[-8:-1] <= 0.005)
                | (recent_candles["high"] >= ema20.iloc[-8:-1])
            )
            touches_50ema = any(recent_candles["high"] >= ema50.iloc[-8:-1])

            if green_count >= 2 and (touches_20ema or touches_50ema):
                if touches_50ema:
                    pullback_type = "50-EMA"

                for rev_idx in [-2, -1]:
                    r_bar = df.iloc[rev_idx]
                    r_open = float(r_bar["open"])
                    r_close = float(r_bar["close"])
                    r_high = float(r_bar["high"])
                    r_low = float(r_bar["low"])
                    r_body = abs(r_close - r_open)
                    r_range = r_high - r_low
                    r_rsi = float(rsi14.iloc[rev_idx])
                    r_vol = float(vol.iloc[rev_idx])

                    if r_range <= 0:
                        continue

                    prev_bar = df.iloc[rev_idx - 1]
                    is_engulfing = (
                        r_close < float(prev_bar["open"])
                        and r_open > float(prev_bar["close"])
                        and r_close < r_open
                    )
                    upper_wick = r_high - max(r_open, r_close)
                    is_shooting_star = (upper_wick > 1.2 * r_body) and ((r_high - r_close) > 0.4 * r_range)
                    is_strong_close = (r_close < r_open) and ((r_high - r_close) > 0.5 * r_range)

                    is_valid_reversal = is_engulfing or is_shooting_star or is_strong_close
                    rsi_ok = 25.0 <= r_rsi <= 70.0
                    vol_ok = r_vol >= 0.70 * avg_vol

                    if is_valid_reversal and rsi_ok and vol_ok:
                        if rev_idx == -2:
                            if curr_close < r_low or (curr_low < r_low and curr_close < curr_open):
                                direction = "SELL"
                                pullback_extreme_high = float(df.iloc[-8:]["high"].max())
                                break
                        elif rev_idx == -1:
                            if is_strong_close and curr_close < float(df.iloc[-2]["low"]):
                                direction = "SELL"
                                pullback_extreme_high = float(df.iloc[-8:]["high"].max())
                                break

        if direction is None:
            return None

        entry_price = curr_close

        # Stop Loss Calculation
        min_sl_dist = entry_price * 0.003
        max_sl_dist = entry_price * 0.010

        if direction == "BUY":
            raw_sl = (pullback_extreme_low or (curr_low * 0.9985)) - (entry_price * 0.0015)
            sl_dist = max(min_sl_dist, min(entry_price - raw_sl, max_sl_dist))
            sl_price = round(entry_price - sl_dist, 2)
            target_price = round(entry_price + (1.8 * sl_dist), 2)
        else:
            raw_sl = (pullback_extreme_high or (curr_high * 1.0015)) + (entry_price * 0.0015)
            sl_dist = max(min_sl_dist, min(raw_sl - entry_price, max_sl_dist))
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(entry_price - (1.8 * sl_dist), 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 1.8

        confidence = 0.80
        if pullback_type == "50-EMA":
            confidence += 0.04
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
                "pullback_depth": pullback_type,
                "rsi14": round(float(rsi14.iloc[-1]), 2),
                "ema20": round(float(ema20.iloc[-1]), 2),
            },
        }
