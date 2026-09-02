import numpy as np
import pandas as pd
from datetime import datetime, time
from typing import Dict, Optional, Any

from ..base import BaseStrategy


class GapFillStrategy(BaseStrategy):
    """Gap Fill: trades gap up/down that show reversal candles, targeting previous close."""

    name = "GapFill"
    description = "Sells gap-ups with red candles, buys gap-downs with green candles. Targets previous close."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Volatile"]
    worst_regimes = []
    params: Dict[str, Any] = {
        "gap_pct_threshold": 0.5,
        "trade_start_time": "09:15",
        "trade_end_time": "10:30",
    }

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        gap_pct_threshold = self.params.get("gap_pct_threshold", 0.5)
        trade_start_str = self.params.get("trade_start_time", "09:15")
        trade_end_str = self.params.get("trade_end_time", "10:30")

        if candles is None or len(candles) < 3:
            return None

        if not all(col in candles.columns for col in ["open", "high", "low", "close", "volume"]):
            return None

        # Time gate
        current_ts = candles.index[-1]
        if isinstance(current_ts, pd.Timestamp):
            current_time = current_ts.time()
        elif 'timestamp' in candles.columns:
            current_time = pd.to_datetime(candles['timestamp'].iloc[-1]).time()
        else:
            return None

        start_time = datetime.strptime(trade_start_str, "%H:%M").time()
        end_time = datetime.strptime(trade_end_str, "%H:%M").time()
        if not (start_time <= current_time <= end_time):
            return None

        # Detect gap: compare first candle open to previous candle close
        prev_close = candles["close"].iloc[-2]
        first_open = candles["open"].iloc[-1]
        first_close = candles["close"].iloc[-1]
        first_high = candles["high"].iloc[-1]
        first_low = candles["low"].iloc[-1]

        gap_pct = ((first_open - prev_close) / prev_close) * 100.0

        direction = None
        entry_price = first_close
        confidence = 0.0

        # Gap up AND first candle is red (bearish) -> SELL (expect gap fill)
        if gap_pct > gap_pct_threshold and first_close < first_open:
            direction = "SELL"
            sl_price = first_high  # SL at day's high so far
            target_price = prev_close  # Target = previous close
            confidence += 0.4

        # Gap down AND first candle is green (bullish) -> BUY (expect gap fill)
        elif gap_pct < -gap_pct_threshold and first_close > first_open:
            direction = "BUY"
            sl_price = first_low  # SL at day's low so far
            target_price = prev_close  # Target = previous close
            confidence += 0.4

        if direction is None:
            return None

        # Validate SL
        if direction == "SELL" and sl_price <= entry_price:
            sl_price = entry_price + abs(gap_pct) / 100.0 * prev_close * 0.5
        elif direction == "BUY" and sl_price >= entry_price:
            sl_price = entry_price - abs(gap_pct) / 100.0 * prev_close * 0.5

        # Stronger gap = higher confidence
        if abs(gap_pct) > 1.0:
            confidence += 0.15
        if abs(gap_pct) > 1.5:
            confidence += 0.1

        # Candle body strength
        candle_body = abs(first_close - first_open)
        candle_range = first_high - first_low
        if candle_range > 0 and candle_body / candle_range > 0.6:
            confidence += 0.15

        # Volume support
        if len(candles) >= 5:
            avg_vol = candles["volume"].iloc[-5:-1].astype(float).mean()
            if avg_vol > 0 and candles["volume"].iloc[-1] > avg_vol * 1.3:
                confidence += 0.1

        # Regime bonus
        if regime == "Volatile":
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
                "gap_pct": round(gap_pct, 3),
                "prev_close": round(prev_close, 2),
                "gap_direction": "up" if gap_pct > 0 else "down",
            },
        }
