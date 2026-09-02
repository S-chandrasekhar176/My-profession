import numpy as np
import pandas as pd
from typing import Dict, Optional, Any

from ..base import BaseStrategy
from utils.indicators import calculate_ema, calculate_vwap, calculate_atr


class NewsMomentumStrategy(BaseStrategy):
    """News Momentum: after a strong initial move (gap > 1%), wait for pullback
    to VWAP/EMA, then enter in the original direction."""

    name = "NewsMomentum"
    description = "Buys/sells pullbacks to VWAP/EMA after a strong initial gap move."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "gap_pct_threshold": 1.0,
        "pullback_ema_period": 9,
        "atr_period": 14,
        "target_atr_mult": 2.0,
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        gap_pct_threshold = self.params.get("gap_pct_threshold", 1.0)
        ema_period = self.params.get("pullback_ema_period", 9)
        atr_period = self.params.get("atr_period", 14)
        target_atr_mult = self.params.get("target_atr_mult", 2.0)

        min_candles = ema_period + atr_period + 10
        if candles is None or len(candles) < min_candles:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Detect initial gap
        prev_close = candles["close"].iloc[-2]
        first_open = candles["open"].iloc[-len(candles)]  # First candle open (day open)
        gap_from_open = ((first_open - prev_close) / prev_close) * 100.0

        # If no timestamp info, approximate gap from first candle
        # Use the earliest available candle's open vs the previous day's last close
        # In practice, this would use yesterday's close
        # Here we use the first candle's open as proxy for day open
        # and measure pullback from recent highs/lows

        # Calculate EMA and VWAP
        ema = calculate_ema(candles["close"], ema_period)
        atr = calculate_atr(candles["high"], candles["low"], candles["close"], atr_period)

        current_ema = ema.iloc[-1]
        current_atr = atr.iloc[-1]
        if np.isnan(current_ema) or np.isnan(current_atr) or current_atr <= 0:
            return None

        # VWAP calculation (needs datetime index)
        df = candles.copy()
        if isinstance(df.index, pd.DatetimeIndex):
            vwap = calculate_vwap(df["high"], df["low"], df["close"], df["volume"].astype(float))
            current_vwap = vwap.iloc[-1]
            if np.isnan(current_vwap):
                current_vwap = None
        else:
            current_vwap = None

        current_close = candles["close"].iloc[-1]
        current_low = candles["low"].iloc[-1]
        current_high = candles["high"].iloc[-1]

        # Detect initial move direction from recent price action
        # Look for a significant move in the last 10 candles
        lookback = min(10, len(candles) - 1)
        move_high = candles["high"].iloc[-lookback:].max()
        move_low = candles["low"].iloc[-lookback:].min()
        move_start = candles["open"].iloc[-lookback]
        move_range = move_high - move_low

        if move_range <= 0:
            return None

        # Determine if initial move was up or down
        # Use position of current close relative to the move range
        move_mid = (move_high + move_low) / 2.0
        initial_direction = None
        gap_size_pct = 0.0

        # Gap up detection
        if first_open > prev_close * (1 + gap_pct_threshold / 100.0):
            initial_direction = "BUY"
            gap_size_pct = gap_from_open
        # Gap down detection
        elif first_open < prev_close * (1 - gap_pct_threshold / 100.0):
            initial_direction = "SELL"
            gap_size_pct = gap_from_open

        # Fallback: if no clear gap but strong recent move, detect direction
        if initial_direction is None:
            recent_move_pct = ((current_close - move_start) / move_start) * 100.0
            if recent_move_pct > gap_pct_threshold:
                initial_direction = "BUY"
                gap_size_pct = recent_move_pct
            elif recent_move_pct < -gap_pct_threshold:
                initial_direction = "SELL"
                gap_size_pct = recent_move_pct

        if initial_direction is None:
            return None

        confidence = 0.0
        direction = initial_direction  # Trade in original direction
        entry_price = current_close

        # Pullback detection: price has pulled back toward EMA or VWAP
        pullback_level = current_ema
        if current_vwap is not None and not np.isnan(current_vwap):
            # Use average of EMA and VWAP as pullback zone
            pullback_level = (current_ema + current_vwap) / 2.0

        # Check if current price is near the pullback level
        pullback_tolerance = current_atr * 0.5
        near_pullback = abs(current_close - pullback_level) <= pullback_tolerance

        # For BUY: price should be pulling back down toward support
        # For SELL: price should be pulling back up toward resistance
        if direction == "BUY" and current_close <= pullback_level + pullback_tolerance:
            confidence += 0.35  # Pullback detected
        elif direction == "SELL" and current_close >= pullback_level - pullback_tolerance:
            confidence += 0.35  # Pullback detected
        else:
            return None  # No pullback yet, wait

        # Gap strength
        if abs(gap_size_pct) > 1.5:
            confidence += 0.15
        if abs(gap_size_pct) > 2.5:
            confidence += 0.1

        # Pullback candle is a reversal candle (hammer/shooting star)
        candle_body = abs(current_close - candles["open"].iloc[-1])
        candle_range = current_high - current_low
        upper_wick = current_high - max(current_close, candles["open"].iloc[-1])
        lower_wick = min(current_close, candles["open"].iloc[-1]) - current_low

        if candle_range > 0:
            if direction == "BUY" and lower_wick > candle_body * 0.5:
                confidence += 0.15  # Hammer-like
            elif direction == "SELL" and upper_wick > candle_body * 0.5:
                confidence += 0.15  # Shooting star-like

        # Volume support
        if len(candles) >= 20:
            avg_vol = candles["volume"].iloc[-20:].astype(float).mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.3:
                confidence += 0.1

        confidence = min(confidence, 1.0)
        if confidence < 0.3:
            return None

        # SL at pullback low (BUY) or pullback high (SELL)
        lookback_sl = min(15, len(candles) - 1)
        if direction == "BUY":
            sl_price = candles["low"].iloc[-lookback_sl:].min()
            risk = entry_price - sl_price
            if risk <= 0:
                sl_price = entry_price - 1.0 * current_atr
                risk = entry_price - sl_price
            if risk <= 0:
                return None
            target_price = entry_price + target_atr_mult * current_atr
        else:
            sl_price = candles["high"].iloc[-lookback_sl:].max()
            risk = sl_price - entry_price
            if risk <= 0:
                sl_price = entry_price + 1.0 * current_atr
                risk = sl_price - entry_price
            if risk <= 0:
                return None
            target_price = entry_price - target_atr_mult * current_atr

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
                "gap_size_pct": round(gap_size_pct, 3),
                "pullback_level": round(pullback_level, 2),
                "current_ema": round(current_ema, 2),
                "vwap": round(current_vwap, 2) if current_vwap is not None and not np.isnan(current_vwap) else None,
                "atr": round(current_atr, 2),
            },
        }
