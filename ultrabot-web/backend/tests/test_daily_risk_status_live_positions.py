"""Regression tests for HOTFIX #7 (2026-09-01 live session).

The DailyRiskStatus object hardoded open_positions=0, capital_in_use=0.0 and
capital_usage_pct=0.0, making the /status risk banner blind to live
positions. The G1 risk GATE was never affected (the engine passes the real
count via _build_risk_context), but every status consumer — including the
operator dashboards — saw zero. These tests pin the pass-through parameters.
"""
import pytest

from risk.daily_risk_manager import DailyRiskManager


@pytest.fixture
def manager():
    return DailyRiskManager(config={"max_daily_loss_pct": 3.0}, total_capital=500_000.0)


def test_status_reflects_live_positions_and_capital(manager):
    """Status object must report the live values passed by the engine."""
    status = manager.check_daily_limits(open_positions_count=2, capital_in_use=40_000.0)
    assert status.open_positions == 2
    assert status.capital_in_use == 40_000.0
    assert status.capital_usage_pct == 8.0  # 40k / 500k = 8%


def test_status_defaults_preserve_old_signature(manager):
    """Zero-arg calls must keep working (backwards compatibility)."""
    status = manager.check_daily_limits()
    assert status.open_positions == 0
    assert status.capital_in_use == 0.0
    assert status.capital_usage_pct == 0.0
    assert status.can_take_new_trades is True


def test_capital_usage_pct_zero_when_no_capital_in_use(manager):
    """Avoid division noise when flat: usage must be exactly 0.0."""
    status = manager.check_daily_limits(open_positions_count=0, capital_in_use=0.0)
    assert status.capital_usage_pct == 0.0


@pytest.mark.asyncio
async def test_async_alias_passes_through_params(manager):
    """get_daily_risk_status (engine alias) must forward the live params."""
    status = await manager.get_daily_risk_status(
        open_positions_count=3, capital_in_use=150_000.0
    )
    assert status.open_positions == 3
    assert status.capital_in_use == 150_000.0
    assert status.capital_usage_pct == 30.0
