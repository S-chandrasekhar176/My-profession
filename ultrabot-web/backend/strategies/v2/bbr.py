"""BBR — Bollinger Band Reversion Strategy (V2).

Purpose: Range-bound / Sideways regime strategy that enters when price touches
or pierces Bollinger Band extremes and reverts toward the SMA(20) middle band.

Complementary to VR (VWAP Reversion):
  - VR uses intraday VWAP as the anchor — resets daily.
  - BBR uses SMA(20) Bollinger Bands — captures multi-candle mean reversion
    patterns that persist across sessions.

Entry Logic:
  - Price closes below lower Bollinger Band (20,2) → BUY
  - Price closes above upper Bollinger Band (20,2) → SELL
  - Bandwidth squeeze filter: %B width must be > 0.5% (avoids ultra-tight bands)
  - RSI(14) divergence confirmation preferred

Risk Management:
  - Stop-loss at SMA(20) ± 3σ
  - Target at SMA(20) middle band
  - Time filter: 09:30–14:30 IST
"""
from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import (
    calculate_bollinger_bands,
    calculate_rsi,
    calculate_atr,
    calculate_sma,
)


class BollingerBandReversion(BaseStrategy):
    """BBR — Bollinger Band Reversion: fades touches of Bollinger Band extremes."""

    name: str = "BBR"
    description: str = "Bollinger Band Reversion fading band-touch extremes back to the SMA(20) middle band."
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
        if candles is None or len(candles) < 25:
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()

        # ── Time filter: 09:30 to 14:30 ───────────────────────────
        if isinstance(df.index, pd.DatetimeIndex):
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < (9 * 60 + 30) or curr_min > (14 * 60 + 30):
                return None

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

        # ── 1. Bollinger Bands (20, 2.0) ──────────────────────────
        upper, middle, lower = calculate_bollinger_bands(close, period=20, num_std=2.0)
        if upper.isna().iloc[-1] or lower.isna().iloc[-1]:
            return None

        curr_upper = float(upper.iloc[-1])
        curr_middle = float(middle.iloc[-1])
        curr_lower = float(lower.iloc[-1])

        # Wider stop band at 3σ
        _, _, lower_3 = calculate_bollinger_bands(close, period=20, num_std=3.0)
        upper_3, _, _ = calculate_bollinger_bands(close, period=20, num_std=3.0)
        curr_upper_3 = float(upper_3.iloc[-1]) if not upper_3.isna().iloc[-1] else curr_upper * 1.005
        curr_lower_3 = float(lower_3.iloc[-1]) if not lower_3.isna().iloc[-1] else curr_lower * 0.995

        # ── 2. Bandwidth filter (avoid ultra-tight squeeze) ───────
        bandwidth = (curr_upper - curr_lower) / curr_middle if curr_middle > 0 else 0
        if bandwidth < 0.005:  # < 0.5% band width = too tight, no edge
            return None

        # ── 3. %B position ────────────────────────────────────────
        band_range = curr_upper - curr_lower
        pct_b = (curr_close - curr_lower) / band_range if band_range > 0 else 0.5

        # ── 4. RSI(14) for confirmation ───────────────────────────
        rsi = calculate_rsi(close, period=14)
        curr_rsi = float(rsi.iloc[-1]) if not rsi.isna().iloc[-1] else 50.0

        # ── 5. ATR for minimum move filter ────────────────────────
        atr = calculate_atr(high, low, close, period=14)
        curr_atr = float(atr.iloc[-1]) if not atr.isna().iloc[-1] else (curr_close * 0.01)

        # ── 6. Signal detection ───────────────────────────────────
        direction = None
        confidence = 0.0
        entry_price = curr_close

        # Oversold: price at or below lower band
        if curr_close <= curr_lower:
            direction = "BUY"
            sl_dist = max(entry_price - curr_lower_3, curr_atr * 0.5, entry_price * 0.003)
            sl_price = entry_price - sl_dist
            target_dist = max(curr_middle - entry_price, curr_atr * 0.6, entry_price * 0.004)
            target_price = entry_price + target_dist
            confidence = 0.40

            # RSI confirmation
            if curr_rsi < 30:
                confidence += 0.20
            elif curr_rsi < 40:
                confidence += 0.10

            # Bullish candle reversal in oversold zone
            if curr_close > curr_open:
                confidence += 0.10

            # Previous candle also touched lower band (double-touch pattern)
            if prev_close <= float(lower.iloc[-2]) if not lower.isna().iloc[-2] else False:
                confidence += 0.05

        # Overbought: price at or above upper band
        elif curr_close >= curr_upper:
            direction = "SELL"
            sl_dist = max(curr_upper_3 - entry_price, curr_atr * 0.5, entry_price * 0.003)
            sl_price = entry_price + sl_dist
            target_dist = max(entry_price - curr_middle, curr_atr * 0.6, entry_price * 0.004)
            target_price = entry_price - target_dist
            confidence = 0.40

            # RSI confirmation
            if curr_rsi > 70:
                confidence += 0.20
            elif curr_rsi > 60:
                confidence += 0.10

            # Bearish candle reversal in overbought zone
            if curr_close < curr_open:
                confidence += 0.10

            # Previous candle also touched upper band
            if prev_close >= float(upper.iloc[-2]) if not upper.isna().iloc[-2] else False:
                confidence += 0.05

        if direction is None:
            return None

        # Sanitize signal geometry
        if direction == "SELL" and not (sl_price > entry_price > target_price):
            return None
        if direction == "BUY" and not (sl_price < entry_price < target_price):
            return None

        # ── 7. Regime bonus ───────────────────────────────────────
        if regime == "Sideways":
            confidence += 0.15
        elif regime in ("Bull", "Bear"):
            confidence += 0.05

        # ── 8. Volume confirmation ────────────────────────────────
        if len(df) >= 20:
            avg_vol = float(vol.iloc[-20:].astype(float).mean())
            curr_vol = float(vol.iloc[-1])
            if avg_vol > 0 and curr_vol > avg_vol * 1.2:
                confidence += 0.10

        confidence = max(0.0, min(confidence, 1.0))
        if confidence < 0.35:
            return None

        # ── 9. Risk/reward ────────────────────────────────────────
        risk_val = abs(entry_price - sl_price)
        reward_val = abs(target_price - entry_price)
        rr = reward_val / risk_val if risk_val > 0 else 0.0

        if rr < 1.0:
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
                "bb_upper": round(curr_upper, 2),
                "bb_middle": round(curr_middle, 2),
                "bb_lower": round(curr_lower, 2),
                "pct_b": round(pct_b, 3),
                "bandwidth": round(bandwidth * 100, 2),
                "rsi_14": round(curr_rsi, 1),
                "atr_14": round(curr_atr, 2),
            },
        }
