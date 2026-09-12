"""Tests for in-memory position cache, EventBus timeout/drain, FeedManager staleness scaling, and G15 volume baseline."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from core.engine import UltraBotEngine, EngineState
from core.event_bus import EventBus, EventPriority
from feeds.feed_manager import FeedManager
from risk.gates.g15_volume_liquidity import G15VolumeLiquidity


class DummyPosition:
    def __init__(self, id=1, symbol="TCS", direction="BUY", entry_price=1000.0,
                 stop_loss=980.0, target=1040.0, quantity=10, current_price=1000.0):
        self.id = id
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.target = target
        self.quantity = quantity
        self.current_price = current_price
        self.trailing_stop = 0.0
        self.trade_id = f"tr_{id}"
        self.strategy = "MRF"


@pytest.mark.asyncio
async def test_active_position_cache_rebuild_and_sync():
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine._active_position_cache = {}

    pos1 = DummyPosition(id=1, symbol="TCS", direction="BUY", entry_price=1000.0, stop_loss=980.0, target=1050.0)
    pos2 = DummyPosition(id=2, symbol="INFY", direction="SELL", entry_price=500.0, stop_loss=510.0, target=480.0)

    # 1. Rebuild cache
    engine._rebuild_active_position_cache([pos1, pos2])
    assert "TCS" in engine._active_position_cache
    assert "INFY" in engine._active_position_cache
    assert engine._active_position_cache["TCS"][0]["stop_loss"] == 980.0

    # 2. Update pos1 (e.g. trailing stop moved)
    pos1.stop_loss = 990.0
    engine._sync_position_to_cache(pos1)
    assert engine._active_position_cache["TCS"][0]["stop_loss"] == 990.0

    # 3. Add pos3
    pos3 = DummyPosition(id=3, symbol="TCS", direction="BUY", entry_price=1005.0, stop_loss=995.0, target=1060.0)
    engine._sync_position_to_cache(pos3)
    assert len(engine._active_position_cache["TCS"]) == 2

    # 4. Remove pos1
    engine._remove_position_from_cache(1, "TCS")
    assert len(engine._active_position_cache["TCS"]) == 1
    assert engine._active_position_cache["TCS"][0]["id"] == 3

    # 5. Remove pos3
    engine._remove_position_from_cache(3, "TCS")
    assert "TCS" not in engine._active_position_cache


@pytest.mark.asyncio
async def test_tick_position_monitor_no_breach_avoids_db():
    """When a tick arrives within bounds, it updates RAM cache and does NOT open DB session."""
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.state = EngineState.RUNNING
    engine._active_position_cache = {}

    pos = DummyPosition(id=1, symbol="RELIANCE", direction="BUY", entry_price=2500.0, stop_loss=2480.0, target=2550.0)
    engine._rebuild_active_position_cache([pos])

    # Mock DB context
    mock_repo_context = MagicMock()
    engine._repo_context = mock_repo_context

    # Normal tick: price = 2510.0 (safe between 2480 and 2550)
    await engine._handle_tick_position_monitor("tick.quote", {"symbol": "RELIANCE", "price": 2510.0})

    # Assert repo context was NEVER touched!
    assert not mock_repo_context.called
    assert engine._active_position_cache["RELIANCE"][0]["current_price"] == 2510.0

    # Unknown symbol tick
    await engine._handle_tick_position_monitor("tick.quote", {"symbol": "SBIN", "price": 800.0})
    assert not mock_repo_context.called


@pytest.mark.asyncio
async def test_tick_position_monitor_breach_triggers_db_exit():
    """When price touches SL, tick monitor opens DB context and triggers position management."""
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.state = EngineState.RUNNING
    engine._active_position_cache = {}

    pos = DummyPosition(id=1, symbol="RELIANCE", direction="BUY", entry_price=2500.0, stop_loss=2480.0, target=2550.0)
    engine._rebuild_active_position_cache([pos])

    # Mock repo
    mock_repo = MagicMock()
    mock_repo.get_open_positions = AsyncMock(return_value=[pos])

    class AsyncContextMock:
        async def __aenter__(self):
            return mock_repo
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

    engine._repo_context = MagicMock(return_value=AsyncContextMock())
    engine._manage_position = AsyncMock()

    # Breaching tick: price drops to 2479.0 <= SL (2480.0)
    await engine._handle_tick_position_monitor("tick.quote", {"symbol": "RELIANCE", "price": 2479.0})

    # Repo context MUST be entered and _manage_position called!
    assert engine._repo_context.called
    assert mock_repo.get_open_positions.called
    assert engine._manage_position.called


def test_feed_manager_staleness_scaling():
    assert FeedManager._get_max_candle_age_minutes("1m") == 5.0
    assert FeedManager._get_max_candle_age_minutes("5m") == 12.0
    assert FeedManager._get_max_candle_age_minutes("15m") == 30.0
    assert FeedManager._get_max_candle_age_minutes("60m") == 120.0
    assert FeedManager._get_max_candle_age_minutes("1d") == 1440.0


@pytest.mark.asyncio
async def test_event_bus_high_priority_timeout():
    """Verify HIGH priority handler timeout prevents deadlock."""
    bus = EventBus()
    bus.start()

    async def hanging_handler(event_name, payload):
        await asyncio.sleep(5.0)

    bus.subscribe("test.event", hanging_handler)
    # Publish HIGH priority
    await bus.publish("test.event", {"data": 1}, priority=EventPriority.HIGH)
    # Wait for dispatch loop to process (it should time out in 2.0s without throwing)
    await asyncio.sleep(2.5)

    await bus.stop(timeout=1.0)
    assert not bus._running


@pytest.mark.asyncio
async def test_g15_staged_baseline_and_breakout_protection():
    gate = G15VolumeLiquidity({})
    # Default mean reversion baseline is 0.50x
    assert gate.mean_reversion_min_volume_ratio == 0.50

    # 1. MRF with volume 0.55x outside midday passes
    res_mrf = await gate.check({"strategy": "MRF", "volume_ratio": 0.55}, {})
    assert res_mrf.passed is True

    # 2. Breakout strategy (e.g. ORB) with 0.70x during midday (12:00) MUST FAIL (preserves 1.00x)
    res_orb = await gate.check({"strategy": "ORB", "volume_ratio": 0.70}, {"time_of_day": "12:00"})
    assert res_orb.passed is False
    assert "below minimum 1.00x" in res_orb.message
