"""Tests for paper broker execution and P&L."""
import pytest

from fees.nse_fee_calculator import NSEFeeCalculator
from brokers.paper_broker import PaperBroker


@pytest.fixture
def broker():
    fee_calc = NSEFeeCalculator(brokerage_per_order=20.0)
    return PaperBroker(initial_capital=100000.0, fee_calculator=fee_calc)


class TestPaperBrokerInit:
    def test_initial_capital(self, broker):
        assert broker.capital == 100000.0

    def test_get_name(self, broker):
        assert broker.get_name() == "paper"

    @pytest.mark.asyncio
    async def test_authenticate(self, broker):
        result = await broker.authenticate()
        assert result["success"] is True


@pytest.mark.asyncio
class TestPaperBrokerOrders:
    async def test_place_buy_order(self, broker):
        # Without feed, price is used as-is for LIMIT orders
        result = await broker.place_order(
            symbol="RELIANCE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            price=2435.0,
            order_type="LIMIT",
        )
        assert result["success"] is True
        assert result["order_id"] is not None

    async def test_buy_creates_position(self, broker):
        await broker.place_order(
            symbol="TCS",
            exchange="NSE",
            transaction_type="BUY",
            quantity=5,
            price=3500.0,
            order_type="LIMIT",
        )
        positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "TCS"
        assert positions[0]["quantity"] == 5

    async def test_close_position_profit(self, broker):
        # Buy at 100
        await broker.place_order(
            symbol="TEST",
            exchange="NSE",
            transaction_type="BUY",
            quantity=100,
            price=100.0,
            order_type="LIMIT",
        )
        # Close at 110
        result = await broker.close_position(
            symbol="TEST",
            exit_price=110.0,
        )
        assert result["success"] is True
        assert result["gross_pnl"] == 1000.0  # (110-100)*100
        assert result["net_pnl"] < result["gross_pnl"]  # fees deducted
        assert result["net_pnl"] > 0  # still profitable
        assert broker.capital > 100000.0

    async def test_close_position_loss(self, broker):
        await broker.place_order(
            symbol="LOSER",
            exchange="NSE",
            transaction_type="BUY",
            quantity=100,
            price=500.0,
            order_type="LIMIT",
        )
        result = await broker.close_position(
            symbol="LOSER",
            exit_price=480.0,
        )
        assert result["gross_pnl"] == -2000.0
        assert result["net_pnl"] < 0
        assert broker.capital < 100000.0

    async def test_close_nonexistent_position(self, broker):
        result = await broker.close_position(symbol="NOPE", exit_price=100.0)
        assert result["success"] is False

    async def test_get_margin(self, broker):
        margin = await broker.get_margin()
        assert margin["total"] == 100000.0
        assert margin["available"] == 100000.0

    async def test_margin_updates_after_buy(self, broker):
        res = await broker.place_order(
            symbol="TCS",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            price=3500.0,
            order_type="LIMIT",
        )
        margin = await broker.get_margin()
        assert margin["used"] == 35000.0
        assert margin["available"] == round(100000.0 - 35000.0 - res.get("fees", 0.0), 2)

    async def test_insufficient_margin(self, broker):
        # Try to buy more than available
        result = await broker.place_order(
            symbol="EXPENSIVE",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1000,
            price=200.0,
            order_type="LIMIT",
        )
        assert result["success"] is False
        assert "Insufficient margin" in result["message"]

    async def test_cancel_order(self, broker):
        order = await broker.place_order(
            symbol="TEST",
            exchange="NSE",
            transaction_type="BUY",
            quantity=1,
            price=10.0,
            order_type="LIMIT",
        )
        cancel_result = await broker.cancel_order(order["order_id"])
        # Paper orders fill immediately, so cancel should fail
        assert cancel_result["success"] is False
        assert "Cannot cancel filled order" in cancel_result["message"]
