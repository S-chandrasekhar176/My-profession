from typing import Dict, Optional, Any
import pandas as pd
import numpy as np

from ..base import BaseStrategy
from utils.indicators import calculate_vwap, calculate_obv, calculate_atr, calculate_sma


class VolumeClimax(BaseStrategy):
    """VC — Volume Climax Strategy (climax + retest entry).

    Phase 1 robustness rework: the strategy NO LONGER enters on the climax
    candle's close. Entering on a >3x-volume climactic bar means buying the
    most extended 5 minutes of the move — the exhaustion risk is maximal and
    slippage is worst exactly there.

    New two-stage logic:
      Stage 1 (detection): identify a directional volume climax (>3x 20-period
      average, highest volume in 20 bars, sudden spike, strong close in the
      directional extreme, VWAP context, OBV confirmation) and REGISTER a
      pending retest setup with the climax candle's geometry.
      Stage 2 (entry): wait for price to pull back into the retest zone
      (a band around the climax candle's midpoint) and enter only when a
      confirmation candle closes back in the climax direction. This turns
      "chasing the climax" into "buying the institutional retest".

    Setups expire after ``retest_max_bars`` bars or if price invalidates
    beyond the climax extreme against the setup direction.
    """

    name: str = "VC"
    description: str = "Volume Climax with institutional retest entry: climax detection, midpoint-zone pullback, confirmed resumption."
    preferred_timeframes = ["5min"]
    best_regimes = ["Bull", "Bear", "Sideways"]
    worst_regimes = ["Volatile"]

    def __init__(self, params: Dict[str, Any] = None):
        super().__init__(params=params)
        # Intraday pending retest setups: symbol -> setup dict.
        # Populated when a climax is detected, consumed/expired on later scans.
        self._pending_retests: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _expire_setup(self, symbol: str) -> None:
        self._pending_retests.pop(symbol, None)

    def _bars_since(self, df: pd.DataFrame, climax_time: Any) -> Optional[int]:
        """Number of completed bars after the climax bar (None if unknown)."""
        if isinstance(df.index, pd.DatetimeIndex) and climax_time is not None:
            try:
                return int((df.index > climax_time).sum()) - 1  # exclude the live forming bar
            except Exception:
                return None
        return None

    def _check_retest_trigger(
        self, df: pd.DataFrame, setup: Dict[str, Any], vwap: float
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a pending retest setup against the latest candle.

        Returns a signal dict on confirmed retest entry, otherwise None.
        Expires/invalidates the setup through the caller when appropriate.
        """
        retest_max_bars = int(self.params.get("retest_max_bars", 12))
        min_rr = float(self.params.get("min_rr", 1.2))
        zone_band = float(self.params.get("retest_zone_band", 0.30))

        curr = df.iloc[-1]
        curr_close = float(curr["close"])
        curr_open = float(curr["open"])
        curr_low = float(curr["low"])
        curr_high = float(curr["high"])

        direction = setup["direction"]
        climax_high = setup["climax_high"]
        climax_low = setup["climax_low"]
        climax_range = climax_high - climax_low
        climax_mid = setup["climax_mid"]
        atr = setup["atr"]
        vol_ratio = setup["vol_ratio"]

        if climax_range <= 0 or curr_close <= 0:
            return None

        # Expiry: too many bars since the climax
        bars_since = self._bars_since(df, setup.get("climax_time"))
        if bars_since is not None and bars_since > retest_max_bars:
            return None  # caller expires

        # Structural invalidation: price already closed beyond the climax
        # extreme against the setup (the climax failed outright).
        if direction == "BUY" and curr_close < climax_low - (0.25 * atr):
            return None
        if direction == "SELL" and curr_close > climax_high + (0.25 * atr):
            return None

        zone_lo = climax_mid - (zone_band * climax_range)
        zone_hi = climax_mid + (zone_band * climax_range)

        if direction == "BUY":
            # Pullback must have traded into the zone ...
            touched_zone = curr_low <= zone_hi
            # ... and the current bar must close back in the climax direction,
            # above the midpoint, with VWAP context intact.
            confirmed = (curr_close > curr_open) and (curr_close > climax_mid) and (curr_close >= vwap)
            if not (touched_zone and confirmed):
                return None

            entry_price = curr_close
            raw_sl = climax_low - (0.10 * atr)
            min_sl = entry_price * 0.003
            max_sl = entry_price * 0.010
            sl_dist = max(min_sl, min(entry_price - raw_sl, max_sl))
            sl_price = round(entry_price - sl_dist, 2)
            # Measured-move projection: climax range added to the climax high
            target_price = round(climax_high + climax_range, 2)
        else:
            touched_zone = curr_high >= zone_lo
            confirmed = (curr_close < curr_open) and (curr_close < climax_mid) and (curr_close <= vwap)
            if not (touched_zone and confirmed):
                return None

            entry_price = curr_close
            raw_sl = climax_high + (0.10 * atr)
            min_sl = entry_price * 0.003
            max_sl = entry_price * 0.010
            sl_dist = max(min_sl, min(raw_sl - entry_price, max_sl))
            sl_price = round(entry_price + sl_dist, 2)
            target_price = round(climax_low - climax_range, 2)

        risk = abs(entry_price - sl_price)
        reward = abs(target_price - entry_price)
        if risk <= 0:
            return None
        risk_reward = round(reward / risk, 2)
        if risk_reward < min_rr:
            return None  # poor geometry — do not force the trade

        confidence = 0.80
        if vol_ratio > 4.0:
            confidence += 0.05
        if abs(curr_close - climax_mid) <= 0.15 * climax_range:
            confidence += 0.05  # tight retest right at the midpoint
        confidence = min(0.92, round(confidence, 2))

        return {
            "symbol": setup["symbol"],
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "sl_price": sl_price,
            "target_price": target_price,
            "confidence": confidence,
            "strategy": self.name,
            "risk_reward": risk_reward,
            "extra_details": {
                "entry_mode": "climax_retest",
                "vol_ratio": round(vol_ratio, 2),
                "vwap": round(vwap, 2),
                "atr": round(atr, 2),
                "retest_zone": [round(zone_lo, 2), round(zone_hi, 2)],
                "climax_high": round(climax_high, 2),
                "climax_low": round(climax_low, 2),
                "bars_since_climax": bars_since if bars_since is not None else -1,
            },
        }

    # ------------------------------------------------------------------
    # Main scan
    # ------------------------------------------------------------------

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        if candles is None or len(candles) < 22:
            return None

        for col in ["open", "high", "low", "close", "volume"]:
            if col not in candles.columns:
                return None

        df = candles.copy()

        # Time filter: entries only in the proven morning / afternoon windows
        # (retest triggers keep the same discipline the old VC had).
        has_dt = isinstance(df.index, pd.DatetimeIndex)
        if has_dt:
            curr_time = df.index[-1].time()
            curr_min = curr_time.hour * 60 + curr_time.minute
            is_morning = (9 * 60 + 15) <= curr_min <= (11 * 60 + 30)
            is_afternoon = (13 * 60) <= curr_min <= (14 * 60 + 30)
            in_entry_window = is_morning or is_afternoon
            current_date_str = str(df.index[-1].date())
        else:
            in_entry_window = True
            current_date_str = "sim_day"

        close = df["close"]
        open_p = df["open"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]

        curr_close = float(close.iloc[-1])
        curr_open = float(open_p.iloc[-1])
        curr_high = float(high.iloc[-1])
        curr_low = float(low.iloc[-1])
        curr_vol = float(vol.iloc[-1])
        prev_vol = float(vol.iloc[-2])

        if curr_close <= 0 or curr_open <= 0:
            return None

        # Shared indicators
        vol_sma = calculate_sma(vol, period=20)
        avg_vol = float(vol_sma.iloc[-2]) if not vol_sma.isna().iloc[-2] else float(vol.iloc[:-1].mean())
        if avg_vol <= 0:
            return None

        vwap_series = calculate_vwap(high, low, close, vol)
        curr_vwap = float(vwap_series.iloc[-1]) if not vwap_series.isna().iloc[-1] else curr_close

        atr_series = calculate_atr(high, low, close, period=14)
        atr = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else curr_close * 0.005

        # ------------------------------------------------------------------
        # Stage 2 first: evaluate any pending retest setup for this symbol.
        # Expiry / invalidation run on EVERY scan (even outside the entry
        # windows) so setups can never linger unmanaged; only the ENTRY
        # trigger respects the entry windows.
        # ------------------------------------------------------------------
        setup = self._pending_retests.get(symbol)
        if setup is not None:
            # New trading day → yesterday's setups are dead
            if current_date_str != setup.get("date", current_date_str):
                self._expire_setup(symbol)
                setup = None

        if setup is not None:
            retest_max_bars = int(self.params.get("retest_max_bars", 12))
            bars_since = self._bars_since(df, setup.get("climax_time"))
            expired = (bars_since is not None and bars_since > retest_max_bars)

            if expired:
                self._expire_setup(symbol)
            else:
                # Structural invalidation: close beyond the climax extreme
                # against the setup direction kills it outright.
                if (
                    (setup["direction"] == "BUY" and curr_close < setup["climax_low"] - 0.25 * atr)
                    or (setup["direction"] == "SELL" and curr_close > setup["climax_high"] + 0.25 * atr)
                ):
                    self._expire_setup(symbol)
                elif in_entry_window and regime != "Volatile":
                    signal = self._check_retest_trigger(df, setup, curr_vwap)
                    if signal is not None:
                        # Setup consumed — one entry per climax
                        self._expire_setup(symbol)
                        return signal

            # A climax bar cannot simultaneously be its own retest — stop here
            # whenever a live setup existed for this symbol this scan.
            return None

        if not in_entry_window:
            return None

        # ------------------------------------------------------------------
        # Stage 1: detect a NEW volume climax and register the retest setup
        # ------------------------------------------------------------------
        vol_ratio = curr_vol / avg_vol
        if vol_ratio < 3.0:
            return None

        lookback_vol = vol.iloc[-20:]
        if curr_vol < lookback_vol.max():
            return None

        prev_vol_ratio = prev_vol / avg_vol
        if prev_vol_ratio >= 2.0:
            return None

        candle_range = curr_high - curr_low
        body = abs(curr_close - curr_open)
        body_pct = body / curr_open
        if body_pct < 0.004 or candle_range <= 0:
            return None

        obv_series = calculate_obv(close, vol)
        curr_obv = float(obv_series.iloc[-1])
        recent_obv = obv_series.iloc[-20:]

        direction = None

        # Bullish climax
        if (
            curr_close > curr_open
            and ((curr_close - curr_low) / candle_range) >= 0.60
            and ((curr_high - curr_close) / candle_range) <= 0.30
            and curr_close >= curr_vwap
            and curr_obv >= recent_obv.max()
        ):
            if regime != "Volatile":
                direction = "BUY"
        # Bearish climax
        elif (
            curr_close < curr_open
            and ((curr_high - curr_close) / candle_range) >= 0.60
            and ((curr_close - curr_low) / candle_range) <= 0.30
            and curr_close <= curr_vwap
            and curr_obv <= recent_obv.min()
        ):
            if regime != "Volatile":
                direction = "SELL"

        if direction is None:
            return None

        # Register the pending retest setup — NO entry on the climax bar itself.
        self._pending_retests[symbol] = {
            "symbol": symbol,
            "date": current_date_str,
            "direction": direction,
            "climax_high": curr_high,
            "climax_low": curr_low,
            "climax_mid": (curr_high + curr_low) / 2.0,
            "atr": atr,
            "vol_ratio": vol_ratio,
            "climax_time": df.index[-1] if has_dt else None,
            "registered_at_bar": len(df),
        }

        logger_note = {
            "registered_climax": direction,
            "vol_ratio": round(vol_ratio, 2),
            "climax_mid": round((curr_high + curr_low) / 2.0, 2),
        }

        # Climax detection produces no tradeable signal this bar — the entry
        # comes later, on the confirmed retest. (Signal emission remains the
        # retest's job, so return None.)
        _ = logger_note
        return None
