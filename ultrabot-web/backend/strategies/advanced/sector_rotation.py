import numpy as np
import pandas as pd
from typing import Dict, Optional, Any, List

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_atr


class SectorRotationStrategy(BaseStrategy):
    """Sector Rotation: identifies sector momentum and picks the strongest stock.

    Simplified implementation: analyzes the given symbol's EMA trend alignment and
    strength relative to recent range. In production, this would reference a list
    of sector stocks and compare their relative momentum.
    """

    name = "SectorRotation"
    description = "Detects sector momentum and enters on the strongest trending stocks."
    preferred_timeframes = ["15min", "5min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = ["Sideways"]
    params: Dict[str, Any] = {
        "ema_short": 9,
        "ema_medium": 21,
        "ema_long": 50,
        "momentum_lookback": 10,
        "momentum_threshold": 1.5,
        "risk_reward_ratio": 2.0,
        "sector_stocks": [],  # List of symbols in same sector
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        ema_short_p = self.params.get("ema_short", 9)
        ema_medium_p = self.params.get("ema_medium", 21)
        ema_long_p = self.params.get("ema_long", 50)
        momentum_lookback = self.params.get("momentum_lookback", 10)
        momentum_threshold = self.params.get("momentum_threshold", 1.5)
        rr_ratio = self.params.get("risk_reward_ratio", 2.0)

        min_candles = ema_long_p + momentum_lookback + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Calculate EMAs
        ema_short = calculate_ema(candles["close"], ema_short_p)
        ema_medium = calculate_ema(candles["close"], ema_medium_p)
        ema_long = calculate_ema(candles["close"], ema_long_p)

        # Calculate ATR
        atr = calculate_atr(candles["high"], candles["low"], candles["close"])

        # Momentum: percentage change over lookback
        price_now = candles["close"].iloc[-1]
        price_prev = candles["close"].iloc[-1 - momentum_lookback]
        momentum_pct = ((price_now - price_prev) / price_prev) * 100.0

        current_ema_short = ema_short.iloc[-1]
        current_ema_medium = ema_medium.iloc[-1]
        current_ema_long = ema_long.iloc[-1]
        current_atr = atr.iloc[-1]

        if any(np.isnan(v) for v in [current_ema_short, current_ema_medium, current_ema_long, current_atr]):
            return None

        if current_atr <= 0:
            return None

        confidence = 0.0
        direction = None
        entry_price = price_now

        # Bullish sector rotation: short > medium > long, momentum positive
        if current_ema_short > current_ema_medium > current_ema_long and momentum_pct > momentum_threshold:
            direction = "BUY"
            confidence += 0.3

        # Bearish sector rotation: short < medium < long, momentum negative
        elif current_ema_short < current_ema_medium < current_ema_long and momentum_pct < -momentum_threshold:
            direction = "SELL"
            confidence += 0.3

        if direction is None:
            return None

        # EMA alignment strength
        if direction == "BUY":
            spread = (current_ema_short - current_ema_long) / current_ema_long * 100
        else:
            spread = (current_ema_long - current_ema_short) / current_ema_long * 100

        if spread > 1.0:
            confidence += 0.2
        if spread > 2.0:
            confidence += 0.1

        # Momentum strength
        if abs(momentum_pct) > momentum_threshold * 1.5:
            confidence += 0.15

        # Volume confirmation
        if len(candles) >= 20:
            avg_vol = candles["volume"].iloc[-20:].astype(float).mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.5:
                confidence += 0.15

        # Regime alignment
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.1

        confidence = min(confidence, 1.0)
        if confidence < 0.35:
            return None

        # SL at recent swing low (for BUY) or swing high (for SELL)
        if direction == "BUY":
            swing_low = candles["low"].iloc[-20:].min()
            sl_price = min(swing_low, entry_price - 1.5 * current_atr)
            risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + rr_ratio * risk
        else:
            swing_high = candles["high"].iloc[-20:].max()
            sl_price = max(swing_high, entry_price + 1.5 * current_atr)
            risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - rr_ratio * risk

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
                "momentum_pct": round(momentum_pct, 3),
                "ema_spread_pct": round(spread, 3),
                "atr": round(current_atr, 2),
            },
        }