from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_rsi, calculate_macd, calculate_vwap, calculate_obv, calculate_sma


class TrendReversalSystem(BaseStrategy):
    """TRS — Trend Reversal System.

    Highest risk/reward, lowest win-rate strategy designed to catch exhaustion reversals
    of mature intraday trends.
    Requires 3 of 4 confirmations: RSI divergence, MACD divergence/crossover, key level/EMA break,
    and volume/OBV confirmation. Mandates halved position size (`half_size: True`).
    """

    name: str = "TRS"
    description: str = "Trend Reversal System with 3-of-4 multi-indicator divergence confirmation and halved sizing."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Volatile"]
    worst_regimes = ["Sideways"]

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

        # Time filter: 11:00 AM to 2:00 PM (14:00)
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < (11 * 60) or curr_min > (14 * 60):
                return None

        # Regime suitability: TRS catches transitions (Shorts in Bull, Longs in Bear)
        if regime == "Sideways":
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

        # 1. Indicators
        ema20 = calculate_ema(close, period=20)
        rsi14 = calculate_rsi(close, period=14)
        macd_line, sig_line, macd_hist = calculate_macd(close, fast_period=12, slow_period=26, signal_period=9)
        vwap_series = calculate_vwap(high, low, close, vol)
        obv_series = calculate_obv(close, vol)
        vol_sma = calculate_sma(vol, period=20)

        if ema20.isna().iloc[-1] or rsi14.isna().iloc[-1] or macd_hist.isna().iloc[-1] or vwap_series.isna().iloc[-1]:
            return None

        curr_vwap = float(vwap_series.iloc[-1])
        avg_vol = float(vol_sma.iloc[-1]) if not vol_sma.isna().iloc[-1] else float(vol.mean())

        direction = None
        confirmations_count = 0
        extreme_swing = None

        # =========================================================================
        # LONG SETUP: Reversing from Downtrend to Uptrend (Bear -> Bull reversal)
        # =========================================================================
        if regime in ["Bear", "Volatile"]:
            # Setup check: EMA20 was falling over prior window
            ema_falling = ema20.iloc[-2] < ema20.iloc[-8] if len(ema20) >= 8 else True
            price_below_vwap = curr_close < curr_vwap

            if ema_falling and price_below_vwap:
                confirmations = 0

                # 1. RSI Bullish Divergence (over last 5-15 candles)
                # Look for price making lower low while RSI makes higher low
                price_min_idx = low.iloc[-15:-1].idxmin() if len(low) >= 16 else low.iloc[:-1].idxmin()
                rsi_at_price_min = float(rsi14.loc[price_min_idx])
                curr_rsi = float(rsi14.iloc[-1])
                price_min_val = float(low.loc[price_min_idx])
                
                rsi_divergence = (
                    (curr_low < price_min_val or abs(curr_low - price_min_val) / price_min_val < 0.005)
                    and (curr_rsi > rsi_at_price_min)
                ) or (curr_rsi < 35.0 and curr_rsi > float(rsi14.iloc[-2]))

                if rsi_divergence:
                    confirmations += 1

                # 2. MACD Bullish Divergence or Signal Cross
                macd_cross = (
                    float(macd_line.iloc[-1]) > float(sig_line.iloc[-1])
                    or float(macd_hist.iloc[-1]) > float(macd_hist.iloc[-2])
                )
                if macd_cross:
                    confirmations += 1

                # 3. Key Level Break
                # Current candle is bullish and closes above 20-EMA or previous candle high
                recent_swing_high = float(high.iloc[-6:-1].max())
                ema_break = (curr_close > float(ema20.iloc[-1]) or curr_close > recent_swing_high) and (
                    curr_close > curr_open
                )
                if ema_break:
                    confirmations += 1

                # 4. Volume Confirmation / OBV Divergence
                obv_rising = float(obv_series.iloc[-1]) > float(obv_series.iloc[-5]) if len(obv_series) >= 5 else False
                vol_spike = float(vol.iloc[-1]) > (1.3 * avg_vol)
                if vol_spike or obv_rising:
                    confirmations += 1

                if confirmations >= 3:
                    direction = "BUY"
                    confirmations_count = confirmations
                    extreme_swing = float(low.iloc[-15:].min())

        # =========================================================================
        # SHORT SETUP: Reversing from Uptrend to Downtrend (Bull -> Bear reversal)
        # =========================================================================
        if regime in ["Bull", "Volatile"] and direction is None:
            ema_rising = ema20.iloc[-2] > ema20.iloc[-8] if len(ema20) >= 8 else True
            price_above_vwap = curr_close > curr_vwap

            if ema_rising and price_above_vwap:
                confirmations = 0

                # 1. RSI Bearish Divergence
                price_max_idx = high.iloc[-15:-1].idxmax() if len(high) >= 16 else high.iloc[:-1].idxmax()
                rsi_at_price_max = float(rsi14.loc[price_max_idx])
                curr_rsi = float(rsi14.iloc[-1])
                price_max_val = float(high.loc[price_max_idx])

                rsi_divergence = (
                    (curr_high > price_max_val or abs(curr_high - price_max_val) / price_max_val < 0.005)
                    and (curr_rsi < rsi_at_price_max)
                ) or (curr_rsi > 65.0 and curr_rsi < float(rsi14.iloc[-2]))

                if rsi_divergence:
                    confirmations += 1

                # 2. MACD Bearish Divergence / Cross
                macd_cross = (
                    float(macd_line.iloc[-1]) < float(sig_line.iloc[-1])
                    or float(macd_hist.iloc[-1]) < float(macd_hist.iloc[-2])
                )
                if macd_cross:
                    confirmations += 1

                # 3. Key Level Breakdown
                recent_swing_low = float(low.iloc[-6:-1].min())
                ema_break = (curr_close < float(ema20.iloc[-1]) or curr_close < recent_swing_low) and (
                    curr_close < curr_open
                )
                if ema_break:
                    confirmations += 1

                # 4. Volume / OBV Confirmation
                obv_falling = float(obv_series.iloc[-1]) < float(obv_series.iloc[-5]) if len(obv_series) >= 5 else False
                vol_spike = float(vol.iloc[-1]) > (1.3 * avg_vol)
                if vol_spike or obv_falling:
                    confirmations += 1

                if confirmations >= 3:
                    direction = "SELL"
                    confirmations_count = confirmations
                    extreme_swing = float(high.iloc[-15:].max())

        if direction is None or extreme_swing is None:
            return None

        entry_price = curr_close

        # Stop Loss Calculation
        # SL = Extreme swing low - 0.1% (BUY) / Extreme swing high + 0.1% (SELL)
        # Min SL: 0.4%, Max SL: 1.5%
        min_sl_dist = entry_price * 0.004
        max_sl_dist = entry_price * 0.015

        if direction == "BUY":
            raw_sl = extreme_swing - (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(entry_price - raw_sl, max_sl_dist))
            sl_price = round(entry_price - sl_dist, 2)
            target_price = round(curr_vwap, 2)
            if target_price <= entry_price:
                target_price = round(entry_price + (2.5 * sl_dist), 2)
        else:
            raw_sl = extreme_swing + (entry_price * 0.001)
            sl_dist = max(min_sl_dist, min(raw_sl - entry_price, max_sl_dist))
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(curr_vwap, 2)
            if target_price >= entry_price:
                target_price = round(entry_price - (2.5 * sl_dist), 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 2.5

        confidence = 0.72
        if confirmations_count == 4:
            confidence += 0.08  # All 4 confirmations present
        confidence = min(0.85, round(confidence, 2))

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
                "half_size": True,  # Mandatory halved sizing for TRS
                "confirmations_count": confirmations_count,
                "extreme_swing": round(extreme_swing, 2),
                "vwap": round(curr_vwap, 2),
                "rsi14": round(float(rsi14.iloc[-1]), 2),
            },
        }
