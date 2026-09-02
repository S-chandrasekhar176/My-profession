import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, AsyncMock

from strategies.registry import StrategyRegistry
from strategies.base import BaseStrategy
from config.settings import Settings
from core.engine import UltraBotEngine


def test_strategy_registry_discovery_v2():
    registry = StrategyRegistry()
    registry.discover()
    strategies = registry.get_all()

    # Verify all 7 V2 strategies are registered under their exact acronyms
    expected_v2_keys = ["ORB", "MB", "PTC", "VC", "SIC", "MRF", "TRS"]
    for key in expected_v2_keys:
        assert key in strategies, f"V2 strategy {key} should be registered in StrategyRegistry"
        strat = strategies[key]
        assert isinstance(strat, BaseStrategy)
        assert strat.name == key
        assert hasattr(strat, "scan")


def test_strategy_activation_config_matches_registry():
    registry = StrategyRegistry()
    registry.discover()
    settings = Settings()

    activation_map = settings.get("strategy_activation", default={})
    regimes = ["Bull", "Bear", "Sideways", "Volatile"]

    for regime in regimes:
        regime_cfg = activation_map.get(regime, {})
        active = regime_cfg.get("active", [])
        assert len(active) > 0, f"Regime {regime} should have at least 1 active strategy"
        for name in active:
            strat = registry.get(name)
            assert strat is not None, f"Configured strategy '{name}' in regime '{regime}' must exist in registry"


@pytest.mark.asyncio
async def test_engine_empty_strategy_list_falls_back_to_regime():
    registry = StrategyRegistry()
    registry.discover()
    settings = Settings()

    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock()

    mock_broker_factory = MagicMock()
    mock_broker_factory.create.return_value = mock_broker

    mock_session_mgr = MagicMock()
    mock_session_mgr.create_session = AsyncMock(return_value="test-session-123")
    mock_session_mgr.get_active_session = AsyncMock(return_value=None)
    mock_session_mgr.get_same_day_session = AsyncMock(return_value=None)

    mock_feed_mgr = MagicMock()
    mock_feed_mgr.connect = AsyncMock()

    engine = UltraBotEngine(
        config=settings,
        repository_getter=MagicMock(),
        error_engine=AsyncMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=mock_broker_factory,
        feed_manager=mock_feed_mgr,
        session_manager=mock_session_mgr,
        strategy_registry=registry,
    )
    engine.current_regime = "Bull"
    
    # Starting engine with empty list [] should fall back to regime defaults, not stay empty
    await engine.start(mode="paper", broker_name="paper", strategy_names=[])
    try:
        assert len(engine.active_strategies) > 0
        assert "ORB" in engine.active_strategies
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_v2_strategies_scan_interface():
    registry = StrategyRegistry()
    registry.discover()

    # Generate synthetic 5-minute candle data
    dates = pd.date_range("2026-08-19 09:15", periods=50, freq="5min")
    df = pd.DataFrame({
        "open": np.linspace(100, 105, 50),
        "high": np.linspace(101, 106, 50),
        "low": np.linspace(99, 104, 50),
        "close": np.linspace(100.5, 105.5, 50),
        "volume": np.random.randint(1000, 5000, size=50),
    }, index=dates)

    expected_v2_keys = ["ORB", "MB", "PTC", "VC", "SIC", "MRF", "TRS"]
    for key in expected_v2_keys:
        strat = registry.get(key)
        assert strat is not None
        # Test scan execution does not crash
        res = await strat.scan(symbol="SBIN", candles=df, regime="Bull", vix=14.0)
        # Scan can return None or dict
        if res is not None:
            assert isinstance(res, dict)
