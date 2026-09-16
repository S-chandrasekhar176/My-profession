"""Tests for G14StrategyBacktest adaptive win-rate logic."""
import pytest
from risk.gates.g14_strategy_backtest import G14StrategyBacktest


@pytest.mark.asyncio
async def test_g14_adaptive_sic_case():
    """SIC scenario from 2026-09-16 live run:

    Win rate = 50.0% (below standard 55% threshold)
    Profit Factor = 1.566 (above 1.50 compensating threshold)
    Total trades = 12 (above 10 min_samples)
    Expected: PASS with adaptive note.
    """
    gate = G14StrategyBacktest({
        "min_backtest_win_rate": 0.55,
        "min_backtest_profit_factor": 1.25,
        "min_backtest_samples": 10,
        "adaptive_win_rate": True,
    })

    signal = {"strategy": "SIC", "symbol": "SUNPHARMA"}
    context = {
        "strategy_stats": {
            "total_trades": 12,
            "win_rate": 0.50,
            "profit_factor": 1.566,
            "source": "live_performance",
        }
    }

    result = await gate.check(signal, context)
    assert result.passed is True
    assert "adaptive win rate" in result.message
    assert result.threshold == 0.48


@pytest.mark.asyncio
async def test_g14_mrf_negative_profit_factor_still_blocked():
    """MRF scenario from 2026-09-16 live run:

    Win rate = 30.77%
    Profit Factor = 0.423 (far below 1.25 minimum)
    Total trades = 13
    Expected: FAIL on Profit Factor.
    """
    gate = G14StrategyBacktest({
        "min_backtest_win_rate": 0.55,
        "min_backtest_profit_factor": 1.25,
        "min_backtest_samples": 10,
        "adaptive_win_rate": True,
    })

    signal = {"strategy": "MRF", "symbol": "DELHIVERY"}
    context = {
        "strategy_stats": {
            "total_trades": 13,
            "win_rate": 0.3077,
            "profit_factor": 0.423,
            "source": "live_performance",
        }
    }

    result = await gate.check(signal, context)
    assert result.passed is False
    assert "profit factor" in result.message.lower()


@pytest.mark.asyncio
async def test_g14_adaptive_scale_levels():
    gate = G14StrategyBacktest({"adaptive_win_rate": True, "min_backtest_win_rate": 0.55})
    assert gate.get_effective_min_win_rate(2.10) == 0.38
    assert gate.get_effective_min_win_rate(1.80) == 0.42
    assert gate.get_effective_min_win_rate(1.60) == 0.48
    assert gate.get_effective_min_win_rate(1.40) == 0.52
    assert gate.get_effective_min_win_rate(1.28) == 0.55


@pytest.mark.asyncio
async def test_g14_insufficient_samples_passes_with_info():
    gate = G14StrategyBacktest({"min_backtest_samples": 10})
    signal = {"strategy": "ORB", "symbol": "RELIANCE"}
    context = {
        "strategy_stats": {
            "total_trades": 5,
            "win_rate": 0.40,
            "profit_factor": 1.10,
        }
    }
    result = await gate.check(signal, context)
    assert result.passed is True
    assert result.severity == "info"
    assert "< 10 required" in result.message
