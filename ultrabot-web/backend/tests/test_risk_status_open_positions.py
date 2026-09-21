"""Tests verifying risk status correctly reflects open positions and capital in use.

Backlog item #7: /api/engine/status and get_dashboard_data() previously called
daily_risk.get_daily_risk_status() without arguments, reporting 0 open positions
and 0.0 capital in use even when active positions were held.
"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.engine import UltraBotEngine
from core.market_hours import MarketHours
from risk.daily_risk_manager import DailyRiskManager


@pytest.mark.asyncio
async def test_engine_status_and_dashboard_report_open_positions_and_capital():
    """Verify get_status() and get_dashboard_data() pass open_positions to daily risk."""
    fake_positions = [
        SimpleNamespace(
            id=1,
            trade_id="t1",
            symbol="RELIANCE",
            direction="BUY",
            strategy="ORB",
            entry_price=2500.0,
            current_price=2550.0,
            quantity=10,
            remaining_qty=10,
            invested_amount=25000.0,
            stop_loss=2400.0,
            target=2700.0,
            entry_time="2026-09-21T10:00:00+05:30",
            extra=None,
        ),
        SimpleNamespace(
            id=2,
            trade_id="t2",
            symbol="INFY",
            direction="BUY",
            strategy="VWAP",
            entry_price=1500.0,
            current_price=1510.0,
            quantity=20,
            remaining_qty=20,
            invested_amount=30000.0,
            stop_loss=1450.0,
            target=1600.0,
            entry_time="2026-09-21T10:15:00+05:30",
            extra=None,
        ),
    ]

    mock_repo = AsyncMock()
    mock_repo.get_open_positions = AsyncMock(return_value=fake_positions)
    mock_repo.get_todays_pnl = AsyncMock(return_value={"net_pnl": 500.0, "total_trades": 2, "wins": 1, "losses": 1})
    mock_repo.get_todays_trades = AsyncMock(return_value=[])
    mock_repo.get_multi_timeframe_fee_summary = AsyncMock(return_value={})
    mock_repo.get_shadow_clock = AsyncMock(return_value={})
    mock_repo.get_feature_snapshot_coverage = AsyncMock(return_value={})

    @asynccontextmanager
    async def _repo_context():
        yield mock_repo

    async def _repo_getter():
        return mock_repo

    daily_risk = DailyRiskManager(
        config={"max_daily_loss_pct": 3.0, "max_daily_trades": 10},
        total_capital=100000.0,
    )

    engine = UltraBotEngine(
        config=MagicMock(),
        repository_getter=_repo_getter,
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=daily_risk,
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
        market_hours=MarketHours(),
    )
    engine._repo_context = _repo_context

    # 1. Test get_status()
    status = await engine.get_status()
    risk_info = status.get("risk", {})
    assert risk_info.get("open_positions") == 2
    assert risk_info.get("capital_in_use") == 55000.0

    # 2. Test get_dashboard_data()
    dashboard = await engine.get_dashboard_data()
    dash_risk = dashboard.get("risk", {})
    assert dash_risk.get("open_positions") == 2
    assert dash_risk.get("capital_in_use") == 55000.0
