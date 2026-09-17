"""VR — VWAP Reversion Strategy (V2).

Purpose: Range-bound / Sideways regime strategy that fades extreme intraday
price deviations from VWAP back toward the mean.

Why this exists (2026-09-16 live run forensic):
  - Today's Sideways regime produced 0 ORB and 0 VC signals because those
    strategies require strong directional breakouts that don't occur in
    range-bound sessions.
  - SIC and MRF were the only signal producers, but MRF had negative
    expectancy (PF 0.42) and was correctly blocked.
  - VR is designed to *thrive* in Sideways by detecting VWAP band extremes
    and fading them, providing signal diversity when trend strategies are quiet.

Entry Logic:
  - Price crosses below VWAP - 2.0σ → BUY (oversold fade)
  - Price crosses above VWAP + 2.0σ → SELL (overbought fade)
  - Confirmed by RSI(5) exhaustion (< 30 or > 70)
  - Volume surge on reversion candle adds confidence

Risk Management:
  - Stop-loss at VWAP ± 3.0σ (wider band)
  - Target at VWAP (mean reversion target)
  - Time filter: 10:00–14:00 IST (avoids opening volatility and closing auction)
"""
from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_vwap, calculate_rsi, calculate_bollinger_bands, calculate_atr


class VWAPReversion(BaseStrategy):
    """VR — VWAP Reversion: fades 2σ+ deviations from intraday VWAP in range-bound markets."""

    name: str = "VR"
    description: str = "VWAP Reversion fading 2σ+ deviations back toward intraday VWAP in Sideways regimes."
    preferred_timeframes = ["5min"]
    best_regimes = ["Sideways"]
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
        if candles is None or len(candles) < 20:
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()

        # ── Time filter: 10:00 AM to 14:00 (2:00 PM) ──────────────
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < (10 * 60) or curr_min > (14 * 60):
                return None

        # Skip volatile regimes — VWAP bands whip too much
        if regime == "Volatile":
            return None

        close = df["close"]
        open_p = df["open"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        curr_close = float(close.iloc[-1])
        curr_open = float(open_p.iloc[-1])
        prev_close = float(close.iloc[-2])

        if curr_close <= 0 or curr_open <= 0:
            return None

        # ── 1. VWAP + Rolling σ Bands ──────────────────────────────
        vwap_series = calculate_vwap(high, low, close, vol)
        if vwap_series.isna().iloc[-1]:
            return None

        curr_vwap = float(vwap_series.iloc[-1])
        rolling_std = close.rolling(20, min_periods=10).std()
        std_val = float(rolling_std.iloc[-1]) if not rolling_std.isna().iloc[-1] else (curr_close * 0.005)

        if std_val <= 0:
            return None

        upper_2 = curr_vwap + (2.0 * std_val)
        lower_2 = curr_vwap - (2.0 * std_val)
        upper_3 = curr_vwap + (3.0 * std_val)
        lower_3 = curr_vwap - (3.0 * std_val)

        # ── 2. RSI(5) for exhaustion confirmation ──────────────────
        rsi = calculate_rsi(close, period=5)
        curr_rsi = float(rsi.iloc[-1]) if not rsi.isna().iloc[-1] else 50.0

        # ── 3. ATR for minimum move filter ─────────────────────────
        atr = calculate_atr(high, low, close, period=14)
        curr_atr = float(atr.iloc[-1]) if not atr.isna().iloc[-1] else (curr_close * 0.01)

        # Minimum deviation must be > 0.5 * ATR to avoid noise
        deviation = abs(curr_close - curr_vwap)
        if deviation < 0.5 * curr_atr:
            return None

        # ── 4. Signal detection ────────────────────────────────────
        direction = None
        confidence = 0.0
        entry_price = curr_close

        # Oversold: price below lower 2σ band → BUY fade
        if curr_close < lower_2:
            direction = "BUY"
            sl_dist = max(entry_price - lower_3, curr_atr * 0.5, entry_price * 0.003)
            sl_price = entry_price - sl_dist
            target_dist = max(curr_vwap - entry_price, curr_atr * 0.6, entry_price * 0.004)
            target_price = entry_price + target_dist
            confidence = 0.40

            # RSI exhaustion confirmation
            if curr_rsi < 30:
                confidence += 0.20
            elif curr_rsi < 40:
                confidence += 0.10

            # Candle reversal: close > open (bullish candle in oversold zone)
            if curr_close > curr_open:
                confidence += 0.10

        # Overbought: price above upper 2σ band → SELL fade
        elif curr_close > upper_2:
            direction = "SELL"
            sl_dist = max(upper_3 - entry_price, curr_atr * 0.5, entry_price * 0.003)
            sl_price = entry_price + sl_dist
            target_dist = max(entry_price - curr_vwap, curr_atr * 0.6, entry_price * 0.004)
            target_price = entry_price - target_dist
            confidence = 0.40

            # RSI exhaustion confirmation
            if curr_rsi > 70:
                confidence += 0.20
            elif curr_rsi > 60:
                confidence += 0.10

            # Candle reversal: close < open (bearish candle in overbought zone)
            if curr_close < curr_open:
                confidence += 0.10

        if direction is None:
            return None

        # Sanitize signal geometry
        if direction == "SELL" and not (sl_price > entry_price > target_price):
            return None
        if direction == "BUY" and not (sl_price < entry_price < target_price):
            return None

        # ── 5. Regime alignment bonus ──────────────────────────────
        if regime == "Sideways":
            confidence += 0.15
        elif regime in ("Bull", "Bear"):
            confidence += 0.05  # Directional regimes can still revert intraday

        # ── 6. Volume confirmation ─────────────────────────────────
        if len(df) >= 20:
            avg_vol = float(vol.iloc[-20:].astype(float).mean())
            curr_vol = float(vol.iloc[-1])
            if avg_vol > 0 and curr_vol > avg_vol * 1.3:
                confidence += 0.10  # Volume surge on reversion supports the move

        # ── 7. Z-score extremity bonus ─────────────────────────────
        z_score = deviation / std_val if std_val > 0 else 0.0
        if z_score > 2.5:
            confidence += 0.05
        if z_score > 3.0:
            confidence += 0.05

        confidence = max(0.0, min(confidence, 1.0))
        if confidence < 0.35:
            return None

        # ── 8. Risk/reward calculation ─────────────────────────────
        risk_val = abs(entry_price - sl_price)
        reward_val = abs(target_price - entry_price)
        rr = reward_val / risk_val if risk_val > 0 else 0.0

        # Minimum R:R of 1.2 required
        if rr < 1.2:
            return None

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
                "vwap": round(curr_vwap, 2),
                "z_score": round(z_score, 3),
                "rsi_5": round(curr_rsi, 1),
                "std_val": round(std_val, 2),
                "upper_2sigma": round(upper_2, 2),
                "lower_2sigma": round(lower_2, 2),
                "atr_14": round(curr_atr, 2),
            },
        }
