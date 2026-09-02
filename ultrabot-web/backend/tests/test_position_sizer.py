"""Comprehensive tests for PositionSizer with tightened 8% Kelly cap and 1% hard risk floor."""
import pytest
from types import SimpleNamespace

from risk.position_sizer import PositionSizer


SIZING_CONFIG = {
    "kelly_min_fraction": 0.02,
    "kelly_max_fraction": 0.08,  # Tightened from 0.25 to 0.08
    "hard_risk_pct": 1.0,        # 1.0% hard capital-risk floor
    "confidence_tiers": {
        "high": {"min": 0.8, "multiplier": 1.0},
        "medium": {"min": 0.6, "multiplier": 0.8},
        "low": {"min": 0.4, "multiplier": 0.5},
    },
    "volatility_tiers": {
        "calm": {"max_vix": 14, "multiplier": 1.0},
        "normal": {"max_vix": 18, "multiplier": 0.85},
        "nervous": {"max_vix": 22, "multiplier": 0.65},
        "fearful": {"max_vix": 999, "multiplier": 0.4},
    },
    "drawdown_tiers": {
        "profit": {"min_pct": 0, "multiplier": 1.0},
        "small_loss": {"min_pct": -1, "multiplier": 0.9},
        "mod_loss": {"min_pct": -2, "multiplier": 0.7},
        "big_loss": {"min_pct": -3, "multiplier": 0.4},
    },
}

CAPITAL_CONFIG = {
    "virtual_capital": 100000,
    "max_capital_usage_pct": 90,
    "min_position_size": 5000,
    "max_per_position_pct": 25,
}


def make_signal(symbol="UNKNOWN", confidence=0.8, entry_price=400.0, sl_price=390.0, **kwargs):
    defaults = {
        "symbol": symbol,
        "confidence": confidence,
        "entry_price": entry_price,
        "sl_price": sl_price,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestPositionSizerBasic:
    @pytest.fixture
    def sizer(self):
        return PositionSizer(SIZING_CONFIG, CAPITAL_CONFIG)

    def test_returns_sizing_result(self, sizer):
        signal = make_signal()
        ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)
        assert result.method == "dynamic_kelly"
        assert result.quantity > 0
        assert result.position_size > 0

    def test_tightened_kelly_cap_at_8_percent(self, sizer):
        """Force maximum confidence (0.95), low VIX (12.0), profit tier (+1.0%).
        Kelly base = min(0.08, 0.95 * 0.25 = 0.2375) -> 0.08.
        Max position value = 100,000 * 0.08 = 8,000.
        """
        signal = make_signal(confidence=0.95, entry_price=100.0, sl_price=99.0)
        ctx = {"vix": 12.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)
        assert result.raw_fraction == 0.08
        assert result.position_size <= 8000.0 + 100.0  # Under 8% cap

    def test_hard_risk_floor_caps_quantity_on_wide_sl(self, sizer):
        """Simulate wide SL distance (3.0% risk per unit):
        Entry = 1000.0, SL = 970.0 -> RiskPerUnit = 30.0.
        Capital = 100,000 -> 1% Hard Risk Cap = 1,000 rupees.
        Max Quantity Allowed = floor(1000 / 30) = 33 shares.
        Total Risk Amount = 33 * 30 = 990 <= 1000.
        """
        signal = make_signal(symbol="INFY", confidence=0.85, entry_price=1000.0, sl_price=970.0, segment="EQ")
        ctx = {"vix": 14.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)

        # Confirm hard risk floor capped the quantity
        assert result.quantity <= 33
        assert result.risk_amount <= 1000.0
        assert result.risk_pct <= 1.0

    def test_high_confidence_gives_more(self, sizer):
        high_signal = make_signal(confidence=0.9, entry_price=200.0, sl_price=198.0)
        low_signal = make_signal(confidence=0.5, entry_price=200.0, sl_price=198.0)
        ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        high_result = sizer.calculate(high_signal, ctx)
        low_result = sizer.calculate(low_signal, ctx)
        assert high_result.quantity >= low_result.quantity

    def test_high_vix_reduces_size(self, sizer):
        signal = make_signal(confidence=0.8, entry_price=200.0, sl_price=198.0)
        calm_ctx = {"vix": 12.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        fearful_ctx = {"vix": 25.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        calm_result = sizer.calculate(signal, calm_ctx)
        fearful_result = sizer.calculate(signal, fearful_ctx)
        assert fearful_result.volatility_multiplier == 0.4
        assert calm_result.quantity >= fearful_result.quantity

    def test_drawdown_reduces_size(self, sizer):
        signal = make_signal(confidence=0.8, entry_price=200.0, sl_price=198.0)
        profit_ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        loss_ctx = {"vix": 15.0, "current_drawdown_pct": -1.5, "available_capital": 100000.0}
        profit_result = sizer.calculate(signal, profit_ctx)
        loss_result = sizer.calculate(signal, loss_ctx)
        assert loss_result.drawdown_multiplier == 0.7
        assert profit_result.quantity >= loss_result.quantity


    def test_fno_uses_lot_size(self, sizer):
        signal = make_signal(symbol="BPCL")
        ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)
        # 1975 = verified current NSE F&O market lot for BPCL (2026-08-27
        # reference data; was 900 before NSE's lot-size revisions were tracked).
        assert result.lot_size == 1975
        assert result.is_equity is False

    def test_equity_any_quantity(self, sizer):
        signal = make_signal(symbol="UNKNOWN")
        ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)
        assert result.is_equity is True
        assert result.lot_size is None
        assert result.quantity > 0

    def test_confidence_tier_labels(self, sizer):
        signal_high = make_signal(confidence=0.85)
        signal_low = make_signal(confidence=0.45)
        ctx = {"vix": 15.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        high = sizer.calculate(signal_high, ctx)
        low = sizer.calculate(signal_low, ctx)
        assert high.confidence_tier == "high"
        assert low.confidence_tier == "low"

    def test_volatility_tier_labels(self, sizer):
        signal = make_signal()
        calm_ctx = {"vix": 12.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        nervous_ctx = {"vix": 20.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        calm = sizer.calculate(signal, calm_ctx)
        nervous = sizer.calculate(signal, nervous_ctx)
        assert calm.volatility_tier == "calm"
        assert nervous.volatility_tier == "nervous"

    def test_expected_performance_kelly_formula(self, sizer):
        """Test feeding strategy expected win rate and risk reward directly into Kelly."""
        # Win rate 58% (0.58), R = 2.0 (Avg win 20, Avg loss 10)
        # Kelly = (0.58 * 2 - (1 - 0.58)) / 2 = (1.16 - 0.42) / 2 = 0.37
        # Half-Kelly = 0.37 * 0.5 = 0.185 -> clamped to kelly_max 0.08
        signal = make_signal(
            confidence=0.7,
            win_rate=0.58,
            avg_win=20.0,
            avg_loss=10.0,
            entry_price=100.0,
            sl_price=99.0,
        )
        ctx = {"vix": 14.0, "current_drawdown_pct": 1.0, "available_capital": 100000.0}
        result = sizer.calculate(signal, ctx)
        assert result.raw_fraction == 0.08
