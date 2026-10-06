"""VEB — Volatility-Expansion Breakout unit tests.

Synthetic-candle fixtures covering the spec (persist/SPEC_VEB_volatility_
expansion_breakout.md): direction-agnostic breakout, wick-fake rejection,
volume participation, VIX floor, regime weighting (Volatile/Bear full,
Bull half, Sideways none), and ATR-scaled risk geometry.
"""
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from strategies.v2.veb import VolatilityExpansionBreakout

IST = None
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover
    pass


def _candles(n=30, base=3500.0, last=None, volumes=None):
    """Flat 5-min candles (range ~base±1) with an optional final breakout candle.

    Flat candles: O=C=base, H=base+1, L=base-1 → TR≈2, ATR(14)≈2.
    `last` = dict overriding the final candle's o/h/l/c.
    """
    start = datetime.now(IST if IST else datetime.now().tzinfo).replace(
        hour=8, minute=50, second=0, microsecond=0
    )
    idx = [start + timedelta(minutes=5 * i) for i in range(n)]
    rows = []
    for i in range(n):
        rows.append({"open": base, "high": base + 1.0, "low": base - 1.0,
                     "close": base, "volume": 1000})
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    if last:
        for k, v in last.items():
            df.iloc[-1, df.columns.get_loc(k)] = v
    if volumes:
        for i, v in volumes.items():
            df.iloc[i, df.columns.get_loc("volume")] = v
    return df


def _breakout_long_candle(base=3500.0, close=None, high=None, low=None, vol=2000):
    return {
        "open": base,
        "high": high if high is not None else base + 6.0,
        "low": low if low is not None else base - 0.5,
        "close": close if close is not None else base + 5.0,
        "volume": vol,
    }


@pytest.mark.asyncio
async def test_long_breakout_in_volatile_regime_fires():
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle())
    sig = await strat.scan("TEST", df, "Volatile", 17.0)
    assert sig is not None
    assert sig["direction"] == "BUY"
    assert sig["strategy"] == "VEB"
    # ATR-scaled risk geometry: SL 1.5×ATR below, target 2×SL above
    atr = sig["extra_details"]["atr"]
    assert sig["sl_price"] == pytest.approx(sig["entry_price"] - 1.5 * atr, abs=0.05)
    assert sig["target_price"] == pytest.approx(sig["entry_price"] + 3.0 * atr, abs=0.05)
    assert sig["risk_reward"] == 2.0


@pytest.mark.asyncio
async def test_short_breakout_in_bear_regime_fires():
    strat = VolatilityExpansionBreakout()
    df = _candles(last={
        "open": 3500.0, "high": 3500.5, "low": 3493.5,
        "close": 3494.0, "volume": 2000,
    })
    sig = await strat.scan("TEST", df, "Bear", 17.0)
    assert sig is not None
    assert sig["direction"] == "SELL"
    atr = sig["extra_details"]["atr"]
    assert sig["sl_price"] > sig["entry_price"] > sig["target_price"]
    assert sig["target_price"] == pytest.approx(sig["entry_price"] - 3.0 * atr, abs=0.05)


@pytest.mark.asyncio
async def test_vix_floor_blocks_at_14():
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle())
    assert await strat.scan("TEST", df, "Volatile", 14.0) is None


@pytest.mark.asyncio
async def test_vix_floor_passes_at_16():
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle())
    assert await strat.scan("TEST", df, "Volatile", 16.0) is not None


@pytest.mark.asyncio
async def test_wick_fake_rejected_insufficient_expansion():
    """Close beyond the range but the candle itself is small — no trade."""
    strat = VolatilityExpansionBreakout()
    # tiny candle that still closes above range_high + buffer:
    # ATR≈2 → buffer≈0.5, need close ≥ 3501.5, but H-L ≤ 1.2×2 = 2.4
    df = _candles(last={"open": 3500.0, "high": 3502.6, "low": 3500.2,
                        "close": 3502.5, "volume": 2000})
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None


@pytest.mark.asyncio
async def test_volume_participation_required():
    """Breakout + expansion on 1.1x volume = unconfirmed — no trade."""
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle(vol=1100))
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None


@pytest.mark.asyncio
async def test_no_breakout_no_signal():
    strat = VolatilityExpansionBreakout()
    df = _candles(last={"open": 3500.0, "high": 3500.5, "low": 3499.5,
                        "close": 3500.0, "volume": 2000})
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None


@pytest.mark.asyncio
async def test_sideways_regime_marginal_breakout_rejected():
    """No regime bonus in Sideways → a marginal breakout (no quality bonuses)
    stays below G10's 0.60 and is rejected. Only high-quality breakouts
    (deep + heavy volume) may fire outside Volatile/Bear, per spec."""
    strat = VolatilityExpansionBreakout()
    # 1.6x volume: passes the 1.5x participation gate but earns no quality bonus
    df = _candles(last=_breakout_long_candle(vol=1600))
    assert await strat.scan("TEST", df, "Sideways", 17.0) is None


@pytest.mark.asyncio
async def test_bull_regime_half_weight_still_fires():
    """Spec decision #2: Bull allowed (half weight) on strong breakouts."""
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle())  # 2x volume → quality bonus
    sig = await strat.scan("TEST", df, "Bull", 17.0)
    assert sig is not None


@pytest.mark.asyncio
async def test_outside_entry_window_no_signal():
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle())
    # push the last candle to 15:00 IST
    idx = list(df.index)
    idx[-1] = idx[-1].replace(hour=15, minute=0)
    df.index = pd.DatetimeIndex(idx)
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None


@pytest.mark.asyncio
async def test_too_few_candles_no_signal():
    strat = VolatilityExpansionBreakout()
    df = _candles(n=20, last=_breakout_long_candle())
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None


@pytest.mark.asyncio
async def test_missing_columns_no_signal():
    strat = VolatilityExpansionBreakout()
    df = _candles(last=_breakout_long_candle()).drop(columns=["volume"])
    assert await strat.scan("TEST", df, "Volatile", 17.0) is None
