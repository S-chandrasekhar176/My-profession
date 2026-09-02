import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import BackgroundTasks
from models.backtest_result import BacktestRequest
from api.routes.backtest import run_backtest


@pytest.mark.asyncio
async def test_backtest_route_execution():
    """Verify run_backtest executes with BacktestRequest parameters."""
    mock_repo = AsyncMock()
    mock_repo.create_backtest_run.return_value = "bt_12345"

    req = BacktestRequest(
        strategy="Breakout",
        symbol="RELIANCE",
        start_date="2025-01-01",
        end_date="2025-08-10",
        timeframe="5min",
        initial_capital=100000.0,
        parameters={"include_fees": True, "apply_risk_gates": True},
    )
    bg = BackgroundTasks()

    res = await run_backtest(req=req, background_tasks=bg, username="admin", repo=mock_repo)
    assert res is not None
    assert "status" in res or "backtest_id" in res or "id" in res
