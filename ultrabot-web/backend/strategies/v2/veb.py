"""VEB — Volatility-Expansion Breakout Strategy (V2, shadow cohort).

Purpose: the offense layer for Volatile/Bear regimes. The existing roster is
mean-reversion-heavy and long-biased: in hostile tape it either goes defensive
(regime pause) or bleeds (SL churn). VEB is direction-agnostic — it trades
WHICHEVER side of a compressed range breaks, exactly when volatility expands.

Spec: persist/SPEC_VEB_volatility_expansion_breakout.md (approved 2026-10-06:
VIX floor 16 with slope handled by regime weighting; Bull allowed at half
weight).

Entry logic (all must hold, 5-min candles):
  1. VIX >= vix_floor (16.0) — hostile-tape environment.
  2. Range = prior 9 candles (45 min), breakout candle excluded.
  3. Close breaks range_high/low by >= breakout_buffer_atr_mult * ATR(14)
     (0.25 ATR — rejects wick-fakes).
  4. Breakout candle range (H-L) >= expansion_atr_mult * ATR(14) (1.2 — the
     candle itself must be volatile, not just positioned outside the range).
  5. Volume >= volume_mult * 20-candle average (1.5x — participation).
  6. Time window 09:45–14:30 IST (skip opening noise; no fresh breakouts
     into close).

Risk: SL = sl_atr_mult (1.5) * ATR beyond entry; target = target_rr (2.0) x
SL distance; breakeven/trailing handled engine-side. G22 re-entry guard and
G0 penny gate apply automatically.

Note on VIX slope (spec condition 1b): scan() receives only the current VIX
level, not history — the "+3%/30min" slope is enforced at the engine level
via regime classification (Volatile = VIX elevated/turning per
RegimeDetector). In-scan, the floor + symbol-level expansion conditions are
the gate; regime contributes confidence weight only.

Shadow validation: registered in strategy_shadow_mode — signals recorded,
never traded. Verdict at effective_n >= 100 vs breakeven 33.3% (RR 2.0).
Success criterion is regime-attributed: Volatile/Bear PF >= 1.25.
"""
from typing import Any, Dict, Optional

import pandas as pd

from ..base import BaseStrategy
from utils.indicators import calculate_atr


class VolatilityExpansionBreakout(BaseStrategy):
    """VEB — trades range breakouts only under volatility expansion."""

    name: str = "VEB"
    description: str = (
        "Volatility-Expansion Breakout: direction-agnostic range break with "
        "ATR-expanded, volume-confirmed candles, gated on hostile-tape VIX."
    )
    preferred_timeframes = ["5min"]
    best_regimes = ["Volatile", "Bear"]
    worst_regimes = ["Sideways"]

    # Spec defaults — overridable via params, mirrored by config.get
    # fallbacks in the engine (no defaults.yaml schema change needed).
    PARAM_DEFAULTS = {
        "vix_floor": 16.0,
        "range_candles": 9,           # 45 min of 5-min candles
        "breakout_buffer_atr_mult": 0.25,
        "expansion_atr_mult": 1.2,
        "volume_mult": 1.5,
        "volume_avg_period": 20,
        "sl_atr_mult": 1.5,
        "target_rr": 2.0,
        "min_confidence": 0.60,       # engine G10 gate requires 0.60
        "entry_start_minutes": 9 * 60 + 45,
        "entry_end_minutes": 14 * 60 + 30,
    }

    def __init__(self, params: Dict[str, Any] = None):
        merged = dict(self.PARAM_DEFAULTS)
        if params:
            merged.update({k: v for k, v in params.items() if v is not None})
        super().__init__(params=merged)

    def _p(self, key: str) -> Any:
        return self.params.get(key, self.PARAM_DEFAULTS.get(key))

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

        # ── Time window: 09:45–14:30 IST ──────────────────────────
        if isinstance(df.index, pd.DatetimeIndex):
            curr = df.index[-1].time()
            curr_min = curr.hour * 60 + curr.minute
            if curr_min < self._p("entry_start_minutes") or curr_min > self._p("entry_end_minutes"):
                return None

        # ── Condition 1: hostile-tape VIX floor ───────────────────
        vix_val = float(vix or 0.0)
        vix_floor = float(self._p("vix_floor"))
        if vix_val < vix_floor:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"].astype(float)

        curr_close = float(close.iloc[-1])
        candle_high = float(high.iloc[-1])
        candle_low = float(low.iloc[-1])
        if curr_close <= 0 or candle_high <= 0 or candle_low <= 0:
            return None

        # ── ATR(14) ───────────────────────────────────────────────
        atr = calculate_atr(high, low, close, period=14)
        curr_atr = float(atr.iloc[-1]) if not atr.isna().iloc[-1] else 0.0
        if curr_atr <= 0:
            curr_atr = curr_close * 0.01  # conservative fallback

        # ── Condition 2: prior-9-candle range (breakout excluded) ─
        n = int(self._p("range_candles"))
        window = df.iloc[-(n + 1):-1]
        if len(window) < n:
            return None
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())
        if range_high <= range_low:
            return None

        # ── Conditions 3+4: breakout depth + expanded candle ──────
        buffer = self._p("breakout_buffer_atr_mult") * curr_atr
        expansion = candle_high - candle_low
        min_expansion = self._p("expansion_atr_mult") * curr_atr

        up_break = curr_close >= range_high + buffer
        down_break = curr_close <= range_low - buffer
        if not (up_break or down_break):
            return None
        if expansion < min_expansion:
            return None  # candle isn't vol-expanded — likely a wick-fake

        direction = "BUY" if up_break else "SELL"

        # ── Condition 5: volume participation ─────────────────────
        vol_period = int(self._p("volume_avg_period"))
        if len(vol) < vol_period + 1:
            return None
        avg_vol = float(vol.iloc[-(vol_period + 1):-1].mean())
        curr_vol = float(vol.iloc[-1])
        if avg_vol <= 0 or curr_vol < self._p("volume_mult") * avg_vol:
            return None

        # ── Risk geometry: ATR-scaled SL, RR-fixed target ─────────
        entry_price = curr_close
        sl_dist = self._p("sl_atr_mult") * curr_atr
        target_dist = self._p("target_rr") * sl_dist
        if direction == "BUY":
            sl_price = entry_price - sl_dist
            target_price = entry_price + target_dist
        else:
            sl_price = entry_price + sl_dist
            target_price = entry_price - target_dist

        # Geometry sanity — a malformed signal is dropped, never repaired
        if direction == "BUY" and not (sl_price < entry_price < target_price):
            return None
        if direction == "SELL" and not (sl_price > entry_price > target_price):
            return None

        # ── Confidence ────────────────────────────────────────────
        confidence = 0.50
        # Regime weighting (spec decision #2: Bull allowed at half weight)
        if regime == "Volatile":
            confidence += 0.15
        elif regime == "Bear":
            confidence += 0.15
        elif regime == "Bull":
            confidence += 0.075
        # Deep breakout (beyond buffer earns extra)
        if up_break:
            depth_atr = (curr_close - range_high) / curr_atr
        else:
            depth_atr = (range_low - curr_close) / curr_atr
        if depth_atr >= 0.5:
            confidence += 0.05
        # Volume surge quality
        if avg_vol > 0 and curr_vol >= 2.0 * avg_vol:
            confidence += 0.05

        confidence = max(0.0, min(confidence, 1.0))
        if confidence < float(self._p("min_confidence")):
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "sl_price": round(sl_price, 2),
            "target_price": round(target_price, 2),
            "confidence": round(confidence, 2),
            "strategy": self.name,
            "risk_reward": self._p("target_rr"),
            "extra_details": {
                "range_high": round(range_high, 2),
                "range_low": round(range_low, 2),
                "atr": round(curr_atr, 2),
                "vix": round(vix_val, 2),
                "candle_expansion_atr": round(expansion / curr_atr, 2),
                "volume_ratio": round(curr_vol / avg_vol, 2) if avg_vol > 0 else 0.0,
                "regime": regime,
            },
        }
