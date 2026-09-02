"""Tests for candle DataFrame construction, DatetimeIndex guarantee, and indicator robustness.

Verifies:
1. candles_to_dataframe correctly assigns pd.DatetimeIndex from timestamps.
2. calculate_vwap never raises AttributeError on RangeIndex or DatetimeIndex.
3. Strategies (MRF, ORB, etc.) execute cleanly on candle lists and DataFrames.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from utils.candle_utils import candles_to_dataframe
from utils.indicators import calculate_vwap
from strategies.v2.mrf import MeanReversionForce
from strategies.v2.orb import OpeningRangeBreakout

IST = ZoneInfo("Asia/Kolkata")


def generate_sample_candles(n: int = 50, start_hour: int = 10, start_min: int = 0):
    """Generate n 5-minute candles for testing."""
    today = datetime.now(IST).date()
    start_dt = datetime(today.year, today.month, today.day, start_hour, start_min, tzinfo=IST)
    candles = []
    base_price = 2500.0

    for i in range(n):
        dt = start_dt + timedelta(minutes=i * 5)
        o = base_price + np.sin(i / 5.0) * 10
        h = o + 5.0
        l = o - 5.0
        c = o + 2.0
        v = 1000 + i * 50
        candles.append({
            "timestamp": dt.isoformat(),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": v,
        })
    return candles


def test_candles_to_dataframe_assigns_datetime_index():
    """Raw candle list converted via candles_to_dataframe must have DatetimeIndex."""
    candles = generate_sample_candles(30)
    df = candles_to_dataframe(candles)

    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert isinstance(df.index, pd.DatetimeIndex)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0].hour == 10


def test_candles_to_dataframe_handles_uppercase_and_empty():
    """Handles uppercase column names and empty lists gracefully."""
    empty_df = candles_to_dataframe([])
    assert empty_df.empty

    none_df = candles_to_dataframe(None)
    assert none_df.empty

    upper_candles = [
        {"Timestamp": "2026-08-22T10:00:00+05:30", "Open": 100, "High": 105, "Low": 95, "Close": 102, "Volume": 500}
    ]
    df = candles_to_dataframe(upper_candles)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert "open" in df.columns
    assert "close" in df.columns


def test_calculate_vwap_with_datetime_index():
    """calculate_vwap groups by date correctly on DatetimeIndex."""
    candles = generate_sample_candles(40)
    df = candles_to_dataframe(candles)

    vwap = calculate_vwap(df["high"], df["low"], df["close"], df["volume"])
    assert isinstance(vwap, pd.Series)
    assert len(vwap) == len(df)
    assert not vwap.isna().any()
    assert (vwap > 2400.0).all() and (vwap < 2600.0).all()


def test_calculate_vwap_with_range_index_safe():
    """calculate_vwap does not raise AttributeError: 'RangeIndex' object has no attribute 'date'."""
    candles = generate_sample_candles(20)
    # Plain DataFrame without DatetimeIndex (default integer RangeIndex)
    df_raw = pd.DataFrame(candles)

    # Must calculate safely without crashing
    vwap = calculate_vwap(df_raw["high"], df_raw["low"], df_raw["close"], df_raw["volume"])
    assert isinstance(vwap, pd.Series)
    assert len(vwap) == 20
    assert not vwap.isna().any()


@pytest.mark.asyncio
async def test_mrf_strategy_runs_on_candles_to_dataframe():
    """MRF strategy scan executes without AttributeError when candle DataFrame is constructed."""
    candles = generate_sample_candles(50, start_hour=11, start_min=0)
    df = candles_to_dataframe(candles)

    strat = MeanReversionForce()
    signal = await strat.scan(symbol="RELIANCE", candles=df, regime="Sideways", vix=15.0)
    # Signal may be None or a Dict, but must not crash
    assert signal is None or isinstance(signal, dict)


@pytest.mark.asyncio
async def test_orb_strategy_runs_on_candles_to_dataframe():
    """ORB strategy scan executes without AttributeError when candle DataFrame is constructed."""
    candles = generate_sample_candles(50, start_hour=9, start_min=15)
    df = candles_to_dataframe(candles)

    strat = OpeningRangeBreakout()
    signal = await strat.scan(symbol="TCS", candles=df, regime="Bull", vix=14.0)
    assert signal is None or isinstance(signal, dict)
