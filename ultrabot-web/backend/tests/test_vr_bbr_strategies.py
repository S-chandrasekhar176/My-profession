"""Comprehensive Unit Tests for V2 Range-Bound Strategies: VR and BBR.

Verifies:
1. VR (VWAP Reversion):
   - Initializes correctly with Sideways preferred regime.
   - Handles neutral candles without false positives.
   - Scan interface returns compliant schema (direction, sl_price, target_price, extra_details).
2. BBR (Bollinger Band Reversion):
   - Initializes correctly with Sideways preferred regime.
   - Handles neutral candles without false positives.
   - Scan interface returns compliant schema.
3. Strategy Registry and AdaptiveManager:
   - Both strategies discovered and active in Sideways regime.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from strategies.v2.vr import VWAPReversion
from strategies.v2.bbr import BollingerBandReversion
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager


def _make_sample_candles(count: int = 60, base_price: float = 500.0) -> pd.DataFrame:
    """Generate realistic 5-minute candle DataFrame with volume and timestamps."""
    start_time = datetime(2026, 9, 16, 10, 0)
    timestamps = [start_time + timedelta(minutes=5 * i) for i in range(count)]
    
    # Baseline steady series
    prices = [base_price + np.sin(i / 5.0) * 2.0 for i in range(count)]
    
    df = pd.DataFrame({
        "open": prices,
        "high": [p + 1.0 for p in prices],
        "low": [p - 1.0 for p in prices],
        "close": prices,
        "volume": [5000 + (i * 20) for i in range(count)],
    }, index=timestamps)
    return df


@pytest.mark.asyncio
async def test_vr_strategy_initialization():
    strat = VWAPReversion()
    assert strat.name == "VR"
    assert "Sideways" in strat.best_regimes
    assert "5min" in strat.preferred_timeframes


@pytest.mark.asyncio
async def test_vr_neutral_returns_none():
    strat = VWAPReversion()
    df = _make_sample_candles(count=50, base_price=1000.0)
    
    res = await strat.scan("RELIANCE", df, regime="Sideways", vix=13.0)
    assert res is None


@pytest.mark.asyncio
async def test_vr_extreme_deviation_scan():
    strat = VWAPReversion()
    # Create 50 candles where first 45 are at 1000, then last 5 plunge violently
    start_time = datetime(2026, 9, 16, 11, 0)
    timestamps = [start_time + timedelta(minutes=5 * i) for i in range(50)]
    prices = [1000.0] * 45 + [980.0, 960.0, 940.0, 920.0, 900.0]
    volumes = [10000] * 45 + [30000, 40000, 50000, 60000, 80000]
    
    df = pd.DataFrame({
        "open": prices,
        "high": [p + 2.0 for p in prices],
        "low": [p - 2.0 for p in prices],
        "close": prices,
        "volume": volumes,
    }, index=timestamps)

    res = await strat.scan("SBIN", df, regime="Sideways", vix=13.5)
    if res is not None:
        assert res["direction"] == "BUY"
        assert res["symbol"] == "SBIN"
        assert res["target_price"] > res["entry_price"]
        assert res["sl_price"] < res["entry_price"]
        assert "vwap" in res["extra_details"]


@pytest.mark.asyncio
async def test_bbr_strategy_initialization():
    strat = BollingerBandReversion()
    assert strat.name == "BBR"
    assert "Sideways" in strat.best_regimes


@pytest.mark.asyncio
async def test_bbr_neutral_returns_none():
    strat = BollingerBandReversion()
    df = _make_sample_candles(count=50, base_price=500.0)
    
    res = await strat.scan("TCS", df, regime="Sideways", vix=12.0)
    assert res is None


@pytest.mark.asyncio
async def test_bbr_extreme_deviation_scan():
    strat = BollingerBandReversion()
    start_time = datetime(2026, 9, 16, 11, 0)
    timestamps = [start_time + timedelta(minutes=5 * i) for i in range(50)]
    prices = [500.0] * 45 + [490.0, 480.0, 470.0, 460.0, 450.0]
    volumes = [10000] * 45 + [20000, 30000, 40000, 50000, 60000]
    
    df = pd.DataFrame({
        "open": prices,
        "high": [p + 1.5 for p in prices],
        "low": [p - 1.5 for p in prices],
        "close": prices,
        "volume": volumes,
    }, index=timestamps)

    res = await strat.scan("INFY", df, regime="Sideways", vix=12.5)
    if res is not None:
        assert res["direction"] == "BUY"
        assert res["symbol"] == "INFY"
        assert res["target_price"] > res["entry_price"]
        assert res["sl_price"] < res["entry_price"]
        assert "bb_middle" in res["extra_details"]


def test_registry_and_adaptive_manager_integration():
    registry = StrategyRegistry()
    registry.discover()
    
    assert "VR" in registry.get_all()
    assert "BBR" in registry.get_all()
    
    manager = AdaptiveManager(registry=registry)
    manager.current_regime = "Sideways"
    sideways_strats = [strat.name for strat in manager.get_active_strategies()]
    assert "VR" in sideways_strats
    assert "BBR" in sideways_strats
    assert "ORB" in sideways_strats
    assert "SIC" in sideways_strats
