"""Dynamic trade-duration estimation (P0.5-a).

Estimates how long a trade should need to reach its target — derived from
LIVE market data at signal time, never from hardcoded per-strategy bands:

    expected_minutes ≈ distance_to_target / price_velocity
    price_velocity   = movement per 5-min candle × time-of-day factor × regime factor

Inputs and their sources (all realtime):
    * ATR(14) on 5m candles — emitted by ORB/MB/VC signals (basis="atr")
    * fallback: SL distance / 1.8 (stops are typically placed 1.5–2.2 ATR
      away, so the stop distance implies the volatility) — basis="sl_proxy"
    * last fallback: 0.4% of entry price per 5m candle (typical F&O stock
      movement) — basis="pct_fallback"

Time-of-day velocity reflects the well-documented Indian intraday U-shape:
the 09:15–10:30 opening drive runs ~2× the speed of the 11:30–13:30 lunch
lull, with an afternoon pickup before the 14:30+ close chop.

The estimate is presented as a RANGE (skewed right — moves stall more often
than they accelerate) and capped at the 15:15 auto-square-off so it can
never promise time the market will not give.
"""

from __future__ import annotations

from datetime import datetime, time as dtime
from typing import Any, Dict, Optional

# Movement multiplier per intraday session (IST).
# The opening hour moves fastest; the lunch lull slowest.
_TOD_WINDOWS = [
    (dtime(9, 15), dtime(10, 30), 1.8),   # opening drive
    (dtime(10, 30), dtime(11, 30), 1.3),  # late morning
    (dtime(11, 30), dtime(13, 30), 0.9),  # lunch lull
    (dtime(13, 30), dtime(14, 30), 1.2),  # afternoon session
    (dtime(14, 30), dtime(15, 15), 0.9),  # close chop
]

# Regime multiplier: trending tape resolves directional trades faster.
_REGIME_FACTORS = {
    "Bull": 1.15,
    "Bear": 1.15,
    "Sideways": 0.85,
    "Volatile": 1.4,
}

_SQUARE_OFF = dtime(15, 15)
_CANDLE_MINUTES = 5
_MIN_MINUTES = 5
# Band skew: optimistic bound (fast tape) vs pessimistic bound (stalled move).
_FAST_FACTOR = 1.4
_SLOW_FACTOR = 1.6
# Stops are typically placed 1.5–2.2 ATR away → implied ATR from SL distance.
_SL_TO_ATR_DIVISOR = 1.8
# Last-resort velocity proxy: 0.4% of price per 5m candle.
_PCT_FALLBACK = 0.004


def time_of_day_factor(now_ist: Optional[datetime] = None) -> float:
    """Velocity multiplier for the current IST intraday session."""
    t = (now_ist or datetime.now()).time() if now_ist is None else now_ist.time()
    for start, end, factor in _TOD_WINDOWS:
        if start <= t < end:
            return factor
    return 1.0  # outside market hours / pre-open


def regime_factor(regime: Optional[str]) -> float:
    """Velocity multiplier for the current market regime."""
    return _REGIME_FACTORS.get((regime or "").title(), 1.0)


def _minutes_until_square_off(now_ist: datetime) -> int:
    """Minutes between now and the 15:15 auto-square-off (IST)."""
    close = now_ist.replace(hour=_SQUARE_OFF.hour, minute=_SQUARE_OFF.minute, second=0, microsecond=0)
    delta = (close - now_ist).total_seconds() / 60.0
    return int(max(0, delta))


def resolve_atr(
    entry_price: float,
    stop_loss: Optional[float],
    atr: Optional[float] = None,
) -> tuple[float, str]:
    """Pick the best available per-5m-candle movement estimate.

    Returns (atr_value, basis) where basis ∈ {"atr", "sl_proxy", "pct_fallback"}
    so consumers know exactly how honest the number is.
    """
    if atr is not None and atr > 0:
        return float(atr), "atr"
    if stop_loss is not None and stop_loss > 0 and entry_price > 0:
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance > 0:
            return sl_distance / _SL_TO_ATR_DIVISOR, "sl_proxy"
    if entry_price > 0:
        return entry_price * _PCT_FALLBACK, "pct_fallback"
    return 0.0, "none"


def estimate_trade_duration(
    entry_price: float,
    target_price: Optional[float],
    stop_loss: Optional[float],
    direction: str = "LONG",
    regime: Optional[str] = None,
    atr: Optional[float] = None,
    now_ist: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Estimate the time a trade needs to travel entry → target.

    Returns None when the inputs cannot support an estimate (no target,
    non-positive entry, zero distance, or unresolvable volatility).
    Otherwise:

        {
          "min_minutes": int,          # optimistic bound
          "max_minutes": int,          # pessimistic bound (capped at square-off)
          "basis": str,                # atr | sl_proxy | pct_fallback
          "velocity_per_5m": float,    # ₹ movement per 5m candle after factors
          "candles_to_target": float,  # expected 5m candles to reach target
          "target_distance": float,    # ₹ distance entry → target
        }
    """
    if not entry_price or entry_price <= 0:
        return None
    if not target_price or target_price <= 0:
        return None

    distance = abs(float(target_price) - float(entry_price))
    if distance <= 0:
        return None

    base_atr, basis = resolve_atr(entry_price, stop_loss, atr)
    if base_atr <= 0:
        return None

    now = now_ist or datetime.now()
    tod = time_of_day_factor(now)
    reg = regime_factor(regime)
    velocity = base_atr * tod * reg
    if velocity <= 0:
        return None

    candles = distance / velocity
    base_minutes = candles * _CANDLE_MINUTES

    # Right-skewed band: fast-tape best case vs stalled-move worst case.
    min_minutes = max(_MIN_MINUTES, int(round(base_minutes / _FAST_FACTOR)))
    max_minutes = int(round(base_minutes * _SLOW_FACTOR))

    # The market stops giving time at 15:15 — cap the estimate there.
    until_close = _minutes_until_square_off(now)
    if until_close > 0:
        max_minutes = min(max_minutes, until_close)
        min_minutes = min(min_minutes, until_close)
    else:
        # Outside trading hours (weekend/holiday/evening): no artificial cap.
        pass

    if max_minutes < min_minutes:
        max_minutes = min_minutes

    return {
        "min_minutes": min_minutes,
        "max_minutes": max_minutes,
        "basis": basis,
        "velocity_per_5m": round(velocity, 2),
        "candles_to_target": round(candles, 1),
        "target_distance": round(distance, 2),
    }
