"""Tests for NSE fee calculator with known values."""
import pytest
from fees.nse_fee_calculator import NSEFeeCalculator


@pytest.fixture
def calc():
    return NSEFeeCalculator(brokerage_per_order=20.0)


class TestEquityIntraday:
    """BUY 40 RELIANCE @ 2435, SELL @ 2485."""

    def test_equity_intraday_total_fees(self, calc):
        """Total fees ~= 83.18 for the specified trade."""
        result = calc.calculate_equity_intraday(
            buy_price=2435.0,
            sell_price=2485.0,
            quantity=40,
        )
        # Sum of all charges
        assert result["brokerage"] == 40.0  # 20 + 20
        assert result["stt"] > 0
        assert result["exchange_txn"] > 0
        assert result["gst"] > 0
        assert result["sebi"] > 0
        assert result["stamp_duty"] > 0
        # Total should be approximately 83.18
        assert abs(result["total"] - 83.18) < 1.0

    def test_equity_intraday_components(self, calc):
        result = calc.calculate_equity_intraday(
            buy_price=2435.0, sell_price=2485.0, quantity=40
        )
        # STT is only on sell side for intraday: sell_turnover * 0.00025
        expected_stt = 2485.0 * 40 * 0.00025
        assert abs(result["stt"] - round(expected_stt, 2)) < 0.01

        # Stamp duty is only on buy side for intraday
        expected_stamp = 2435.0 * 40 * 0.00003
        assert abs(result["stamp_duty"] - round(expected_stamp, 2)) < 0.01


class TestEquityDelivery:
    def test_delivery_fees(self, calc):
        result = calc.calculate_equity_delivery(
            buy_price=2435.0,
            sell_price=2485.0,
            quantity=40,
        )
        assert result["brokerage"] == 0.0  # 0 for delivery
        # STT on both sides for delivery
        assert result["stt"] > 0
        # Total should be higher than intraday (no brokerage but more STT)
        assert result["total"] > 0

    def test_delivery_stt_both_sides(self, calc):
        result = calc.calculate_equity_delivery(
            buy_price=1000.0, sell_price=1100.0, quantity=10
        )
        expected_stt = (1000.0 * 10 * 0.001) + (1100.0 * 10 * 0.001)
        assert abs(result["stt"] - round(expected_stt, 2)) < 0.01


class TestOptions:
    def test_options_fees(self, calc):
        result = calc.calculate_options(
            buy_premium=150.0,
            sell_premium=200.0,
            quantity=1,
            lot_size=250,
        )
        assert result["brokerage"] == 40.0  # 20 + 20
        assert result["stt"] > 0  # 0.0625% on sell premium
        # Stamp duty is 0 for options
        assert result["stamp_duty"] == 0.0
        assert result["total"] > 0

    def test_options_stt_sell_only(self, calc):
        result = calc.calculate_options(
            buy_premium=100.0,
            sell_premium=100.0,
            quantity=2,
            lot_size=250,
        )
        # STT = sell_premium * total_qty * 0.000625
        expected_stt = 100.0 * 2 * 250 * 0.000625
        assert abs(result["stt"] - round(expected_stt, 2)) < 0.01


class TestNetPnl:
    def test_long_intraday_net_pnl(self, calc):
        result = calc.calculate_net_pnl(
            entry_price=2435.0,
            exit_price=2485.0,
            quantity=40,
            direction="LONG",
            segment="EQ",
            mode="intraday",
        )
        gross = (2485.0 - 2435.0) * 40  # 2000
        assert result["gross_pnl"] == 2000.0
        assert result["net_pnl"] == gross - result["fees"]
        assert result["net_pnl"] > 0

    def test_short_intraday_net_pnl(self, calc):
        result = calc.calculate_net_pnl(
            entry_price=2485.0,
            exit_price=2435.0,
            quantity=40,
            direction="SHORT",
            segment="EQ",
            mode="intraday",
        )
        assert result["gross_pnl"] == 2000.0
        assert result["net_pnl"] > 0

    def test_losing_trade_net_pnl(self, calc):
        result = calc.calculate_net_pnl(
            entry_price=2485.0,
            exit_price=2435.0,
            quantity=40,
            direction="LONG",
            segment="EQ",
            mode="intraday",
        )
        assert result["gross_pnl"] == -2000.0
        assert result["net_pnl"] < 0

    def test_options_net_pnl(self, calc):
        result = calc.calculate_net_pnl(
            entry_price=150.0,
            exit_price=200.0,
            quantity=1,
            direction="LONG",
            segment="OPT",
            mode="intraday",
            lot_size=250,
        )
        gross = (200.0 - 150.0) * 1 * 250  # 12500
        assert result["gross_pnl"] == 12500.0
        assert result["net_pnl"] == gross - result["fees"]
