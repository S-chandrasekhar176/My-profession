import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_supertrend, calculate_rsi, calculate_atr


class TrendExhaustionStrategy(BaseStrategy):
    """Trend Exhaustion: counter-trend entry after 3+ consecutive Supertrend candles
    with declining RSI and declining volume."""

    name = "TrendExhaustion"
    description = "Detects exhausted trends via Supertrend streak + declining RSI + falling volume."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bear"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "st_period": 10,
        "st_multiplier": 3.0,
        "rsi_period": 14,
        "min_streak": 3,
        "atr_period": 14,
        "risk_reward_ratio": 1.5,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        st_period = self.params.get("st_period", 10)
        st_multiplier = self.params.get("st_multiplier", 3.0)
        rsi_period = self.params.get("rsi_period", 14)
        min_streak = self.params.get("min_streak", 3)
        atr_period = self.params.get("atr_period", 14)
        rr_ratio = self.params.get("risk_reward_ratio", 1.5)

        min_candles = st_period + rsi_period + min_streak + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Indicators
        st_value, st_dir = calculate_supertrend(
            candles["high"], candles["low"], candles["close"],
            period=st_period, multiplier=st_multiplier,
        )
        rsi = calculate_rsi(candles["close"], rsi_period)
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)

        current_atr = atr.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            return None

        # Check recent Supertrend streak
        recent_dirs = st_dir.iloc[-(min_streak + 2):-1]  # Exclude last candle for reversal check
        if recent_dirs.isna().any():
            return None

        # Check for bullish streak (all +1) -> look for bearish reversal
        bullish_streak = 0
        for i in range(len(recent_dirs) - 1, -1, -1):
            if recent_dirs.iloc[i] == 1:
                bullish_streak += 1
            else:
                break

        # Check for bearish streak (all -1) -> look for bullish reversal
        bearish_streak = 0
        for i in range(len(recent_dirs) - 1, -1, -1):
            if recent_dirs.iloc[i] == -1:
                bearish_streak += 1
            else:
                break

        direction = None
        streak_count = 0
        if bullish_streak >= min_streak:
            direction = "SELL"  # Counter-trend: expect reversal
            streak_count = bullish_streak
        elif bearish_streak >= min_streak:
            direction = "BUY"  # Counter-trend: expect reversal
            streak_count = bearish_streak

        if direction is None:
            return None

        # Check RSI is declining (exhaustion)
        rsi_recent = rsi.iloc[-(streak_count + 5):]
        if rsi_recent.isna().any():
            return None

        rsi_early = rsi_recent.iloc[:len(rsi_recent) // 2].mean()
        rsi_late = rsi_recent.iloc[len(rsi_recent) // 2:].mean()
        rsi_declining = rsi_late < rsi_early

        # Check volume declining
        vol_recent = candles["volume"].astype(float).iloc[-(streak_count + 5):]
        vol_early = vol_recent.iloc[:len(vol_recent) // 2].mean()
        vol_late = vol_recent.iloc[len(vol_recent) // 2:].mean()
        vol_declining = vol_late < vol_early * 0.9  # At least 10% decline

        confidence = 0.3  # Base for streak detected

        if rsi_declining:
            confidence += 0.25
        if vol_declining:
            confidence += 0.2

        # Stronger streak = higher confidence
        if streak_count >= 5:
            confidence += 0.1
        if streak_count >= 8:
            confidence += 0.1

        # RSI in extreme zone
        current_rsi = rsi.iloc[-1]
        if not np.isnan(current_rsi):
            if direction == "SELL" and current_rsi > 70:
                confidence += 0.1
            elif direction == "BUY" and current_rsi < 30:
                confidence += 0.1

        # Reversal candle confirmation: current candle closes opposite to trend
        last_open = candles["open"].iloc[-1]
        last_close = candles["close"].iloc[-1]
        if direction == "SELL" and last_close < last_open:
            confidence += 0.1
        elif direction == "BUY" and last_close > last_open:
            confidence += 0.1

        # Regime bonus
        if regime == "Bear":
            confidence += 0.05

        confidence = min(confidence, 1.0)
        if confidence < 0.35:
            return None

        entry_price = last_close

        # SL at recent swing high (for SELL) or swing low (for BUY)
        lookback_sl = min(20, len(candles) - 1)
        if direction == "SELL":
            sl_price = candles["high"].iloc[-lookback_sl:].max()
            risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - rr_ratio * risk
        else:
            sl_price = candles["low"].iloc[-lookback_sl:].min()
            risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + rr_ratio * risk

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
                "streak_count": streak_count,
                "current_rsi": round(float(current_rsi), 2),
                "rsi_declining": rsi_declining,
                "vol_declining": vol_declining,
                "atr": round(current_atr, 2),
            },
        }
