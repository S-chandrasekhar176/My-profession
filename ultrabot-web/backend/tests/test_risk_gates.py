"""Tests for all 13 risk gates with pass and fail scenarios."""
import pytest
from types import SimpleNamespace

from utils.market_utils import get_stock_sector

from risk.gates.g1_max_positions import G1MaxPositions
from risk.gates.g2_sector_concentration import G2SectorConcentration
from risk.gates.g3_max_position_size import G3MaxPositionSize
from risk.gates.g4_max_daily_trades import G4MaxDailyTrades
from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
from risk.gates.g6_correlation_check import G6CorrelationCheck
from risk.gates.g7_vix_filter import G7VIXFilter
from risk.gates.g8_time_of_day import G8TimeOfDay
from risk.gates.g9_price_mismatch import G9PriceMismatch
from risk.gates.g10_min_confidence import G10MinConfidence
from risk.gates.g11_max_drawdown import G11MaxDrawdown
from risk.gates.g12_margin_check import G12MarginCheck
from risk.gates.g13_duplicate_signal import G13DuplicateSignal
from risk.gates.g14_strategy_backtest import G14StrategyBacktest
from risk.gates.g15_volume_liquidity import G15VolumeLiquidity
from risk.gates.g16_multi_timeframe import G16MultiTimeframe


# Default config matching defaults.yaml
DEFAULT_CONFIG = {
    "max_open_positions": 5,
    "max_per_sector": 2,
    "max_daily_trades": 10,
    "max_daily_loss_pct": 3,
    "max_consecutive_losses": 5,
    "vix_threshold": 20,
    "price_mismatch_threshold_pct": 0.5,
    "min_signal_confidence": 0.6,
    "max_drawdown_pct": 5,
}


def make_signal(**kwargs):
    """Create a mock signal with given attributes."""
    defaults = {
        "symbol": "RELIANCE",
        "direction": "LONG",
        "entry_price": 2435.0,
        "sl_price": 2410.0,
        "target_price": 2500.0,
        "confidence": 0.8,
        "strategy_name": "Breakout",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def make_context(**kwargs):
    defaults = {
        "open_positions_count": 0,
        "vix": 15.0,
        "daily_trade_count": 0,
        "daily_pnl_pct": 0.0,
        "current_drawdown_pct": 0.0,
        "capital_in_use_pct": 0.0,
        "broker_ltp": 2435.0,
        "available_capital": 100000.0,
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.asyncio
class TestG1MaxPositions:
    async def test_fail_at_max(self):
        gate = G1MaxPositions(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(open_positions_count=5)
        result = await gate.check(signal, ctx)
        assert result.passed is False
        assert result.gate_name == "G1_MaxPositions"

    async def test_pass_below_max(self):
        gate = G1MaxPositions(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(open_positions_count=3)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG2SectorConcentration:
    async def test_fail_sector_full(self):
        gate = G2SectorConcentration(DEFAULT_CONFIG)
        signal = make_signal()
        # v0.4.11: sector taxonomy is now dynamic (TradingView) — build the
        # fixture from the live attribution instead of a hardcoded name.
        reliance_sector = get_stock_sector("RELIANCE")
        ctx = make_context(
            positions_by_sector={reliance_sector: 2},
        )
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_sector_available(self):
        gate = G2SectorConcentration(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(
            positions_by_sector={get_stock_sector("RELIANCE"): 1},
        )
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG3MaxPositionSize:
    async def test_fail_oversized(self):
        gate = G3MaxPositionSize(DEFAULT_CONFIG)
        signal = make_signal(entry_price=2435.0)
        ctx = make_context(
            total_capital=100000,
            available_capital=30000,
            position_value=30000.0,  # 30% > 25% max
        )
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_normal_size(self):
        gate = G3MaxPositionSize(DEFAULT_CONFIG)
        signal = make_signal(entry_price=2435.0)
        ctx = make_context(
            total_capital=100000,
            available_capital=15000,
            position_value=15000.0,  # 15% < 25% max
        )
        result = await gate.check(signal, ctx)
        assert result.passed is True



@pytest.mark.asyncio
class TestG4MaxDailyTrades:
    async def test_fail_at_limit(self):
        gate = G4MaxDailyTrades(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(daily_trades=10)
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_below_limit(self):
        gate = G4MaxDailyTrades(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(daily_trades=5)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG5MaxDailyLoss:
    async def test_fail_loss_exceeded(self):
        gate = G5MaxDailyLoss(DEFAULT_CONFIG)
        signal = make_signal()
        # 3% of 100000 = 3000. daily_pnl of -4000 exceeds that
        ctx = make_context(daily_pnl=-4000.0, total_capital=100000)
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_within_limit(self):
        gate = G5MaxDailyLoss(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(daily_pnl=-1000.0, total_capital=100000)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG6CorrelationCheck:
    async def test_pass_no_correlation(self):
        gate = G6CorrelationCheck(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(open_positions=[{"symbol": "TCS"}])
        result = await gate.check(signal, ctx)
        # Should pass – no correlation concern with different sectors
        assert result.passed is True


@pytest.mark.asyncio
class TestG7VIXFilter:
    async def test_fail_high_vix(self):
        gate = G7VIXFilter(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(vix=25)
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_normal_vix(self):
        gate = G7VIXFilter(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(vix=15)
        result = await gate.check(signal, ctx)
        assert result.passed is True

    async def test_pass_missing_vix(self):
        """VIX not available should pass by default."""
        gate = G7VIXFilter(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context()
        ctx.pop("vix", None)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG8TimeOfDay:
    async def test_within_window(self):
        gate = G8TimeOfDay(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context()
        # Gate checks real clock; just verify it returns a result
        result = await gate.check(signal, ctx)
        assert result.gate_name == "G8_TimeOfDay"


@pytest.mark.asyncio
class TestG9PriceMismatch:
    async def test_fail_large_mismatch(self):
        gate = G9PriceMismatch(DEFAULT_CONFIG)
        signal = make_signal(entry_price=2450.0)
        ctx = make_context(broker_ltp=2435.0)  # ~0.6% mismatch
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_small_mismatch(self):
        gate = G9PriceMismatch(DEFAULT_CONFIG)
        signal = make_signal(entry_price=2437.0)
        ctx = make_context(broker_ltp=2435.0)  # ~0.08% mismatch
        result = await gate.check(signal, ctx)
        assert result.passed is True

    async def test_pass_no_ltp(self):
        """No broker LTP should pass by default."""
        gate = G9PriceMismatch(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context()
        ctx.pop("broker_ltp", None)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG10MinConfidence:
    async def test_fail_low_confidence(self):
        gate = G10MinConfidence(DEFAULT_CONFIG)
        signal = make_signal(confidence=0.4)
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_high_confidence(self):
        gate = G10MinConfidence(DEFAULT_CONFIG)
        signal = make_signal(confidence=0.8)
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG11MaxDrawdown:
    async def test_fail_deep_drawdown(self):
        gate = G11MaxDrawdown(DEFAULT_CONFIG)
        signal = make_signal()
        # Gate compares current_drawdown > max_drawdown_pct (positive values)
        ctx = make_context(current_drawdown_pct=6.0)
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_pass_shallow_drawdown(self):
        gate = G11MaxDrawdown(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context(current_drawdown_pct=2.0)
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG12MarginCheck:
    async def test_pass_sufficient_margin(self):
        gate = G12MarginCheck(DEFAULT_CONFIG)
        signal = make_signal(entry_price=100.0)
        # RELIANCE lot_size=500 (verified 2026-08-27), equity margin 25%:
        # required = 100*500*0.25 = 12500, available = 50000
        ctx = make_context(
            available_capital=50000.0,
            total_capital=100000.0,
        )
        result = await gate.check(signal, ctx)
        assert result.passed is True

    async def test_fail_insufficient_margin(self):
        gate = G12MarginCheck(DEFAULT_CONFIG)
        signal = make_signal(entry_price=500.0)
        # RELIANCE lot_size=500, equity margin 25%: required = 500*500*0.25 = 62500
        ctx = make_context(
            available_capital=50000.0,
            total_capital=100000.0,
        )
        result = await gate.check(signal, ctx)
        assert result.passed is False

    async def test_equity_symbols_with_ce_pe_substrings_not_misclassified(self):
        """Regression (Phase 5): 'CE' is a substring of RELIAN(CE) and 'PE' of
        (PE)TRONET — bare substring checks misclassified these equities as
        option contracts and inflated margin 4x. Only symbols embedding a
        strike before CE/PE (e.g. RELIANCE29600CE) are options."""
        gate = G12MarginCheck(DEFAULT_CONFIG)
        # qty threaded via context (500 units) for both cases. Ample capital so
        # both cases PASS and GateResult.value exposes the required margin.
        ctx = make_context(
            available_capital=150000.0,
            total_capital=200000.0,
            quantity=500,
        )
        # RELIANCE equity: required = 100 * 500 * 0.25 = 12,500
        equity_signal = make_signal(symbol="RELIANCE", entry_price=100.0)
        result = await gate.check(equity_signal, ctx)
        assert result.passed is True
        assert result.value == 12500.0

        # True option contract (strike before CE): required = 100 * 500 * 1.0 = 50,000
        option_signal = make_signal(symbol="RELIANCE29600CE", entry_price=100.0)
        result = await gate.check(option_signal, ctx)
        assert result.value == 50000.0


@pytest.mark.asyncio
class TestG13DuplicateSignal:
    async def test_pass_no_repo(self):
        """Without a repository, G13 should pass by default."""
        gate = G13DuplicateSignal(DEFAULT_CONFIG)
        signal = make_signal()
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is True


@pytest.mark.asyncio
class TestG14StrategyBacktest:
    async def test_pass_high_win_rate(self):
        gate = G14StrategyBacktest(DEFAULT_CONFIG)
        signal = make_signal(strategy="vwap_breakout")
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is True
        assert result.gate_name == "G14_StrategyBacktest"

    async def test_pass_with_verified_live_stats(self):
        """Real per-strategy stats from the trades ledger (engine context)."""
        gate = G14StrategyBacktest(DEFAULT_CONFIG)
        signal = make_signal(strategy="vwap_breakout")
        ctx = make_context(
            strategy_stats={
                "win_rate": 0.62,
                "profit_factor": 1.45,
                "total_trades": 40,
                "source": "db_strategy_performance",
            }
        )
        result = await gate.check(signal, ctx)
        assert result.passed is True
        assert "Edge verified" in result.message

    async def test_fail_low_backtest_win_rate(self):
        """Real stats below threshold must block (no fabricated profiles)."""
        gate = G14StrategyBacktest({"min_backtest_win_rate": 0.65})
        signal = make_signal(strategy="breakout")
        ctx = make_context(
            strategy_stats={
                "win_rate": 0.52,
                "profit_factor": 1.15,
                "total_trades": 210,
                "source": "db_strategy_performance",
            }
        )
        result = await gate.check(signal, ctx)
        assert result.passed is False
        assert "below minimum requirement" in result.message

    async def test_insufficient_history_passes_with_honest_note(self):
        """No stats anywhere -> honest pass-through, never fabricated numbers."""
        gate = G14StrategyBacktest(DEFAULT_CONFIG)
        signal = make_signal(strategy="brand_new_strategy")
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is True
        assert "insufficient history" in result.message
        assert result.value is None

    async def test_small_sample_passes_with_info_note(self):
        """Below min_samples: stats shown for information only, gate passes."""
        gate = G14StrategyBacktest(DEFAULT_CONFIG)
        signal = make_signal(strategy="sparse_strategy")
        ctx = make_context(
            strategy_stats={
                "win_rate": 0.30,
                "profit_factor": 0.80,
                "total_trades": 3,
                "source": "db_strategy_performance",
            }
        )
        result = await gate.check(signal, ctx)
        assert result.passed is True
        assert "statistical confidence" in result.message


@pytest.mark.asyncio
class TestG15VolumeLiquidity:
    async def test_pass_high_volume(self):
        gate = G15VolumeLiquidity(DEFAULT_CONFIG)
        signal = make_signal(volume_ratio=1.5)
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is True

    async def test_fail_low_volume(self):
        gate = G15VolumeLiquidity({"min_volume_ratio": 1.2})
        signal = make_signal(volume_ratio=0.8)
        ctx = make_context()
        result = await gate.check(signal, ctx)
        assert result.passed is False
        assert "below minimum" in result.message


@pytest.mark.asyncio
class TestG16MultiTimeframe:
    async def test_pass_aligned_trend(self):
        gate = G16MultiTimeframe(DEFAULT_CONFIG)
        signal = make_signal(direction="LONG")
        ctx = make_context(trend="bullish")
        result = await gate.check(signal, ctx)
        assert result.passed is True

    async def test_fail_counter_trend(self):
        gate = G16MultiTimeframe(DEFAULT_CONFIG)
        signal = make_signal(direction="LONG")
        ctx = make_context(trend="bearish")
        result = await gate.check(signal, ctx)
        assert result.passed is False
        assert "counter-trend trap risk" in result.message

