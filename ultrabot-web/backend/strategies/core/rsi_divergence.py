import numpy as np
import pandas as pd
from typing import Dict, Optional, Any, List

from ..base import BaseStrategy
from utils.indicators import calculate_rsi, calculate_atr


def _find_swing_highs(series: pd.Series, window: int = 5) -> List[int]:
    """Find indices of local swing highs."""
    swings = []
    for i in range(window, len(series) - window):
        if series.iloc[i] >= series.iloc[i - window:i].max() and series.iloc[i] >= series.iloc[i + 1:i + 1 + window].max():
            swings.append(i)
    return swings


def _find_swing_lows(series: pd.Series, window: int = 5) -> List[int]:
    """Find indices of local swing lows."""
    swings = []
    for i in range(window, len(series) - window):
        if series.iloc[i] <= series.iloc[i - window:i].min() and series.iloc[i] <= series.iloc[i + 1:i + 1 + window].min():
            swings.append(i)
    return swings


class RSIDivergenceStrategy(BaseStrategy):
    """RSI Divergence: detects bullish/bearish divergence between price and RSI."""

    name = "RSIDivergence"
    description = "Finds RSI divergences indicating potential reversals."
    preferred_timeframes = ["5min", "15min"]
    best_regimes = ["Bear", "Sideways"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "rsi_period": 14,
        "lookback": 40,
        "swing_window": 5,
        "atr_period": 14,
        "risk_reward_ratio": 2.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        rsi_period = self.params.get("rsi_period", 14)
        lookback = self.params.get("lookback", 40)
        swing_window = self.params.get("swing_window", 5)
        atr_period = self.params.get("atr_period", 14)
        rr_ratio = self.params.get("risk_reward_ratio", 2.0)

        min_candles = lookback + rsi_period + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Calculate RSI and ATR
        rsi = calculate_rsi(candles["close"], rsi_period)
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)

        # Slice to recent lookback window
        recent_prices = candles["close"].iloc[-lookback:]
        recent_rsi = rsi.iloc[-lookback:]
        recent_highs = candles["high"].iloc[-lookback:]
        recent_lows = candles["low"].iloc[-lookback:]

        if recent_rsi.isna().all():
            return None

        current_atr = atr.iloc[-1]
        if np.isnan(current_atr) or current_atr <= 0:
            return None

        confidence = 0.0
        direction = None
        entry_price = candles["close"].iloc[-1]
        sl_price = None
        target_price = None

        # --- Bearish Divergence ---
        # RSI makes lower high while price makes higher high
        price_swing_highs = _find_swing_highs(recent_prices, swing_window)
        if len(price_swing_highs) >= 2:
            last_two_price_highs = price_swing_highs[-2:]
            ph1 = recent_prices.iloc[last_two_price_highs[0]]
            ph2 = recent_prices.iloc[last_two_price_highs[1]]

            rsi_h1 = recent_rsi.iloc[last_two_price_highs[0]]
            rsi_h2 = recent_rsi.iloc[last_two_price_highs[1]]

            if not (np.isnan(rsi_h1) or np.isnan(rsi_h2)):
                # Price higher high, RSI lower high = bearish divergence
                if ph2 > ph1 and rsi_h2 < rsi_h1:
                    # Confirmation: current close below the recent swing low
                    recent_price_lows = _find_swing_lows(recent_prices, swing_window)
                    if recent_price_lows:
                        last_low_idx = recent_price_lows[-1]
                        last_low_price = recent_prices.iloc[last_low_idx]
                        if entry_price < last_low_price:
                            direction = "SELL"
                            sl_price = ph2  # Recent swing high
                            risk = sl_price - entry_price
                            if risk > 0:
                                target_price = entry_price - rr_ratio * risk
                                confidence += 0.5

        # --- Bullish Divergence ---
        # RSI makes higher low while price makes lower low
        price_swing_lows = _find_swing_lows(recent_prices, swing_window)
        if len(price_swing_lows) >= 2:
            last_two_price_lows = price_swing_lows[-2:]
            pl1 = recent_prices.iloc[last_two_price_lows[0]]
            pl2 = recent_prices.iloc[last_two_price_lows[1]]

            rsi_l1 = recent_rsi.iloc[last_two_price_lows[0]]
            rsi_l2 = recent_rsi.iloc[last_two_price_lows[1]]

            if not (np.isnan(rsi_l1) or np.isnan(rsi_l2)):
                # Price lower low, RSI higher low = bullish divergence
                if pl2 < pl1 and rsi_l2 > rsi_l1:
                    # Confirmation: current close above the recent swing high
                    recent_price_highs = _find_swing_highs(recent_prices, swing_window)
                    if recent_price_highs:
                        last_high_idx = recent_price_highs[-1]
                        last_high_price = recent_prices.iloc[last_high_idx]
                        if entry_price > last_high_price:
                            direction = "BUY"
                            sl_price = pl2  # Recent swing low
                            risk = entry_price - sl_price
                            if risk > 0:
                                target_price = entry_price + rr_ratio * risk
                                confidence += 0.5

        if direction is None or sl_price is None or target_price is None:
            return None

        # RSI zone bonus
        current_rsi = rsi.iloc[-1]
        if not np.isnan(current_rsi):
            if direction == "SELL" and current_rsi > 60:
                confidence += 0.15
            elif direction == "BUY" and current_rsi < 40:
                confidence += 0.15

        # Volume confirmation
        if len(candles) >= 5:
            vol_surge = candles["volume"].iloc[-1] > candles["volume"].iloc[-5:-1].astype(float).mean() * 1.3
            if vol_surge:
                confidence += 0.15

        # Regime bonus
        if direction == "SELL" and regime in ["Bear", "Sideways"]:
            confidence += 0.1
        elif direction == "BUY" and regime in ["Bull", "Sideways"]:
            confidence += 0.1

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
                "current_rsi": round(float(current_rsi), 2),
                "atr": round(current_atr, 2),
                "divergence_type": "bearish" if direction == "SELL" else "bullish",
            },
        }
