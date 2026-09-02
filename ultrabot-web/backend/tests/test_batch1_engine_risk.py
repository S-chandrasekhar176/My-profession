import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock
from typing import Optional, Dict
from core.engine import UltraBotEngine
from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry
from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
from risk.risk_engine import RiskEngine


class MockDataFrameStrategy(BaseStrategy):
    name = "mock_df_strat"

    async def scan(
        self,
        symbol: str,
        candles: pd.DataFrame,
        regime: str,
        vix: float,
    ) -> Optional[Dict]:
        assert isinstance(candles, pd.DataFrame), f"Expected DataFrame, got {type(candles)}"
        assert isinstance(symbol, str) and symbol == "RELIANCE"
        assert "close" in candles.columns
        assert len(candles) == 2
        return {
            "symbol": symbol,
            "direction": "BUY",
            "entry_price": 2500.0,
            "sl_price": 2450.0,
            "target_price": 2600.0,
            "confidence": 0.85,
            "strategy": self.name,
            "risk_reward": 2.0,
        }


@pytest.mark.asyncio
async def test_engine_strategy_scan_receives_dataframe():
    registry = StrategyRegistry()
    registry.register(MockDataFrameStrategy)

    engine = UltraBotEngine(
        config={},
        repository_getter=MagicMock(),
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
        strategy_registry=registry,
    )
    candles_list = [
        {"timestamp": "2026-08-19T09:15:00", "open": 2480, "high": 2510, "low": 2470, "close": 2495, "volume": 10000},
        {"timestamp": "2026-08-19T09:20:00", "open": 2495, "high": 2505, "low": 2490, "close": 2500, "volume": 15000},
    ]

    res = await engine._execute_strategy_scan(
        symbol="RELIANCE",
        candles=candles_list,
        strategy_name="mock_df_strat",
        regime="Bull",
        vix=14.5,
    )

    assert res is not None
    assert res["symbol"] == "RELIANCE"
    assert res["direction"] == "BUY"
    assert res["entry_price"] == 2500.0


@pytest.mark.asyncio
async def test_g5_daily_loss_gate_blocking():
    gate = G5MaxDailyLoss({"max_daily_loss_pct": 3.0})
    signal = {"symbol": "INFY", "entry_price": 1800.0}

    # Case 1: Normal PnL passes
    res_pass = await gate.check(signal, {"total_capital": 100000.0, "daily_pnl": -1000.0})
    assert res_pass.passed is True

    # Case 2: Negative daily_pnl exceeding 3% fails
    res_fail = await gate.check(signal, {"total_capital": 100000.0, "daily_pnl": -3500.0})
    assert res_fail.passed is False
    assert "Daily P&L" in res_fail.message

    # Case 3: daily_loss key fallback from engine context exceeding 3% fails
    res_fail_loss = await gate.check(signal, {"total_capital": 100000.0, "daily_loss": 3200.0})
    assert res_fail_loss.passed is False
    assert res_fail_loss.value == -3200.0


@pytest.mark.asyncio
async def test_risk_engine_evaluate_context_defaults():
    re = RiskEngine(config={})
    signal = {
        "symbol": "TCS",
        "direction": "BUY",
        "entry_price": 3800.0,
        "quantity": 10,
        "strategy": "ORB",
    }

    # Calling evaluate with minimal context
    result = await re.evaluate(signal=signal, context={"capital": 100000.0, "daily_loss": 500.0})
    assert result is not None
    assert isinstance(result.passed, bool)
