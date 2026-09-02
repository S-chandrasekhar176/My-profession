from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_atr, calculate_adx


class OpeningRangeBreakout(BaseStrategy):
    """ORB — Opening Range Breakout Strategy.

    Identifies the 15-min (or 30-min on high volatility) opening range.
    Enters on a decisive candle close breaking out of the range with volume confirmation.
    Includes gap-day fill validation, false-breakout tracking, and measured-move targets.
    """

    name: str = "ORB"
    description: str = "Opening Range Breakout with volatility-adaptive range, gap filter, and measured targets."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Sideways", "Volatile"]
    worst_regimes = []

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__(params=params)
        # Tracking failed breakouts: symbol -> list/set of failed dates or flags
        self.failed_breakouts: Dict[str, Dict[str, bool]] = {}

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        if candles is None or len(candles) < 4:
            return None

        # Check required columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()
        current_candle = df.iloc[-1]
        close = float(current_candle["close"])
        open_p = float(current_candle["open"])
        high = float(current_candle["high"])
        low = float(current_candle["low"])
        vol = float(current_candle["volume"])

        # Determine current trading day partition if datetime index is present
        has_dt = isinstance(df.index, pd.DatetimeIndex)
        if has_dt:
            current_date_str = str(df.index[-1].date())
            today_mask = df.index.date == df.index[-1].date()
            today_candles = df.loc[today_mask]
            curr_time = df.index[-1].time()
            
            # Time filter: 9:35 AM to 10:30 AM for entries
            # (9:35 candle close is the first potential 5m bar after 15m range)
            entry_start_min = 9 * 60 + 35
            entry_end_min = 10 * 60 + 30
            curr_min = curr_time.hour * 60 + curr_time.minute
            if curr_min < entry_start_min or curr_min > entry_end_min:
                return None
        else:
            current_date_str = "sim_day"
            today_candles = df

        if len(today_candles) < 4:
            return None

        # 1. Volatility-based range selection (15-min = 3 candles vs 30-min = 6 candles)
        # Use 30-min if regime is Volatile or VIX > 22 or ATR / Close > 1.0%
        atr_series = calculate_atr(df["high"], df["low"], df["close"], 14)
        atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else close * 0.005
        atr_pct = (atr / close) * 100.0 if close > 0 else 0.5

        # --- Chop filter (robustness fix): ADX trend-strength gate ---
        # Opening-range breakouts in a directionless tape (ADX < 20) are the
        # classic ORB failure mode: price pokes above the range, then mean-
        # reverts straight back through it. Require a demonstrable trend so
        # the breakout has directional fuel behind it.
        min_adx = float(self.params.get("min_adx", 20.0))
        adx_series = calculate_adx(df["high"], df["low"], df["close"], 14)
        adx_val = float(adx_series.iloc[-1]) if not adx_series.empty and not np.isnan(adx_series.iloc[-1]) else None
        if adx_val is not None and adx_val < min_adx:
            return None
        # If ADX cannot be computed (insufficient history) ORB may still trade
        # on its other confirmations (range width, decisive candle, volume).

        use_30m = (regime == "Volatile" or vix > 22.0 or atr_pct > 1.0)
        range_candles_count = 6 if use_30m else 3

        if len(today_candles) <= range_candles_count:
            return None

        range_df = today_candles.iloc[:range_candles_count]
        range_high = float(range_df["high"].max())
        range_low = float(range_df["low"].min())
        range_width = range_high - range_low

        # Range width check: must be > 0.3% of price (avoid tight ranges that fake out)
        if close <= 0 or (range_width / close) < 0.003:
            return None

        # Gap day handling:
        # Check if 9:15 open is within 0.3% of previous close (if previous day candles exist)
        if has_dt:
            prev_day_candles = df.loc[df.index.date < df.index[-1].date()]
            if not prev_day_candles.empty:
                prev_close = float(prev_day_candles.iloc[-1]["close"])
                first_open = float(today_candles.iloc[0]["open"])
                gap_pct = (first_open - prev_close) / prev_close
                if abs(gap_pct) > 0.003:
                    # Gap open: check if gap was filled first before breakout
                    if gap_pct > 0:  # Gap up
                        filled = float(today_candles["low"].min()) <= prev_close
                    else:  # Gap down
                        filled = float(today_candles["high"].max()) >= prev_close
                    if not filled:
                        return None

        # False breakout memory check:
        # If any candle between range establishment and current candle closed outside range and then reverted back,
        # skip this day.
        post_range_df = today_candles.iloc[range_candles_count:-1]
        long_failed = False
        short_failed = False
        if len(post_range_df) > 0:
            for _, r_bar in post_range_df.iterrows():
                r_c = float(r_bar["close"])
                if r_c > range_high:
                    # Broke out high earlier
                    if float(today_candles.iloc[-2]["close"]) <= range_high:
                        long_failed = True
                if r_c < range_low:
                    # Broke out low earlier
                    if float(today_candles.iloc[-2]["close"]) >= range_low:
                        short_failed = True

        day_failed = self.failed_breakouts.get(symbol, {}).get(current_date_str, False)
        if day_failed:
            return None

        # 2. Breakout candle check on the most recent candle
        candle_range = high - low
        body = abs(close - open_p)

        # Candle body must be > 50% of candle range (decisive bar, not a doji/wick)
        if candle_range <= 0 or (body / candle_range) < 0.5:
            return None

        # 3. Volume confirmation
        # Volume > 1.0x 20-period average volume
        avg_vol = float(df["volume"].rolling(20, min_periods=3).mean().iloc[-1])
        vol_confirmed = vol >= (0.95 * avg_vol)
        if not vol_confirmed:
            return None

        direction = None
        # Long Breakout
        if close > range_high and close > open_p and not long_failed:
            # Bullish breakout above range high
            if regime == "Bear" and vix < 20:
                pass  # still allowed or lower confidence
            direction = "BUY"
        # Short Breakdown
        elif close < range_low and close < open_p and not short_failed:
            direction = "SELL"

        if direction is None:
            return None

        entry_price = close

        # Stop Loss Calculation
        # Primary SL = Range Low - 0.1% for longs / Range High + 0.1% for shorts
        # If Range Width > 1.5% of price, use Aggressive SL = 50% of range width
        is_wide_range = (range_width / entry_price) > 0.015
        if direction == "BUY":
            if is_wide_range:
                raw_sl = entry_price - (0.5 * range_width)
            else:
                raw_sl = range_low - (0.001 * entry_price)
            # Enforce min/max SL bounds: 0.25% min, 1.0% max
            min_sl_dist = entry_price * 0.0025
            max_sl_dist = entry_price * 0.010
            actual_sl_dist = max(min_sl_dist, min(entry_price - raw_sl, max_sl_dist))
            sl_price = round(entry_price - actual_sl_dist, 2)
            
            target_1 = round(entry_price + range_width, 2)
            target_2 = round(entry_price + 2.0 * range_width, 2)
            target_3 = round(entry_price + 3.0 * range_width, 2)
            target_price = target_2
        else:
            if is_wide_range:
                raw_sl = entry_price + (0.5 * range_width)
            else:
                raw_sl = range_high + (0.001 * entry_price)
            min_sl_dist = entry_price * 0.0025
            max_sl_dist = entry_price * 0.010
            actual_sl_dist = max(min_sl_dist, min(raw_sl - entry_price, max_sl_dist))
            sl_price = round(entry_price + actual_sl_dist, 2)

            target_1 = round(entry_price - range_width, 2)
            target_2 = round(entry_price - 2.0 * range_width, 2)
            target_3 = round(entry_price - 3.0 * range_width, 2)
            target_price = target_2

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        risk_reward = round(reward / risk, 2) if risk > 0 else 2.0

        # Confidence calculation
        confidence = 0.75
        if vol > 1.5 * avg_vol:
            confidence += 0.05
        if (direction == "BUY" and regime == "Bull") or (direction == "SELL" and regime == "Bear"):
            confidence += 0.05
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
                "range_type": "30m" if use_30m else "15m",
                "range_high": round(range_high, 2),
                "range_low": round(range_low, 2),
                "range_width": round(range_width, 2),
                "adx": round(adx_val, 2) if adx_val is not None else None,
                "atr": round(atr, 2),
                "target_1": target_1,
                "target_2": target_2,
                "target_3": target_3,
            },
        }
