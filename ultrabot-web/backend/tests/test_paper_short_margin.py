"""Unit tests for PaperBroker short sale margin locking, capital accounting, and CP-07 fix."""
import pytest
from brokers.paper_broker import PaperBroker
from fees.nse_fee_calculator import NSEFeeCalculator


@pytest.fixture
def broker():
    fee_calc = NSEFeeCalculator(brokerage_per_order=20.0)
    return PaperBroker(initial_capital=100000.0, fee_calculator=fee_calc)


@pytest.mark.asyncio
class TestPaperShortSaleMargin:
    async def test_short_sale_insufficient_margin_rejected(self, broker):
        """Short sale must be blocked if required margin exceeds available capital."""
        # Initial capital = 100,000. Try shorting 200,000 worth of stock.
        res = await broker.place_order(
            symbol="BIG_SHORT",
            exchange="NSE",
            transaction_type="SELL",
            quantity=100,
            price=2000.0,
            order_type="LIMIT",
        )
        assert res["success"] is False
        assert "Insufficient margin" in res["message"]
        assert broker.capital == 100000.0

    async def test_short_sale_locks_margin_not_crediting_cash(self, broker):
        """Short sale must lock margin and deduct fees; must NOT credit cash (CP-07 fix)."""
        res = await broker.place_order(
            symbol="INFY",
            exchange="NSE",
            transaction_type="SELL",
            quantity=10,
            price=1500.0,
            order_type="LIMIT",
        )
        assert res["success"] is True
        order_value = 15000.0
        fees = res["fees"]
        
        # Capital must have DECREASED by (order_value + fees), NOT increased
        expected_capital = round(100000.0 - order_value - fees, 2)
        assert round(broker.capital, 2) == expected_capital

        margin = await broker.get_margin()
        assert margin["used"] == order_value
        assert margin["available"] == expected_capital
        assert margin["total"] == round(expected_capital + order_value, 2)

        positions = await broker.get_positions()
        assert len(positions) == 1
        assert positions[0]["direction"] == "SHORT"
        assert positions[0]["quantity"] == 10

    async def test_close_short_position_profit(self, broker):
        """Closing a short position at lower price must return locked margin and add net P&L."""
        # Short 100 @ 200 = 20,000 order value
        open_res = await broker.place_order(
            symbol="SHORT_PROFIT",
            exchange="NSE",
            transaction_type="SELL",
            quantity=100,
            price=200.0,
            order_type="LIMIT",
        )
        assert open_res["success"] is True

        # Close short at 180 (profit of 20 per share = 2,000 gross)
        close_res = await broker.close_position(
            symbol="SHORT_PROFIT",
            exit_price=180.0,
        )
        assert close_res["success"] is True
        assert close_res["gross_pnl"] == 2000.0
        assert close_res["net_pnl"] > 0
        assert close_res["net_pnl"] < 2000.0  # after fees
        
        # Final capital must be initial_capital + net_pnl
        assert round(broker.capital, 2) == round(100000.0 + close_res["net_pnl"], 2)

    async def test_close_short_position_loss(self, broker):
        """Closing a short position at higher price must deduct loss from capital."""
        # Short 50 @ 1000 = 50,000 order value
        open_res = await broker.place_order(
            symbol="SHORT_LOSS",
            exchange="NSE",
            transaction_type="SELL",
            quantity=50,
            price=1000.0,
            order_type="LIMIT",
        )
        assert open_res["success"] is True

        # Close short at 1050 (loss of 50 per share = -2,500 gross)
        close_res = await broker.close_position(
            symbol="SHORT_LOSS",
            exit_price=1050.0,
        )
        assert close_res["success"] is True
        assert close_res["gross_pnl"] == -2500.0
        assert close_res["net_pnl"] < -2500.0  # includes fees
        assert round(broker.capital, 2) == round(100000.0 + close_res["net_pnl"], 2)

    async def test_close_short_via_buy_order(self, broker):
        """Placing an opposing BUY order closes the short position and settles P&L."""
        # Short 10 @ 500 = 5,000
        open_res = await broker.place_order(
            symbol="OPP_TEST",
            exchange="NSE",
            transaction_type="SELL",
            quantity=10,
            price=500.0,
            order_type="LIMIT",
        )
        assert open_res["success"] is True

        # Opposing BUY 10 @ 450 (profit of 50 * 10 = +500)
        buy_res = await broker.place_order(
            symbol="OPP_TEST",
            exchange="NSE",
            transaction_type="BUY",
            quantity=10,
            price=450.0,
            order_type="LIMIT",
        )
        assert buy_res["success"] is True
        pos = broker.positions["OPP_TEST"]
        assert pos["status"] == "CLOSED"
        # Gross = 500, minus entry fees of buy order
        assert pos["realized_pnl"] > 0
        assert broker.capital > 100000.0

    async def test_short_rehydration_locks_capital(self, broker):
        """Rehydrating an open short position locks capital correctly."""
        db_rows = [{
            "id": "pos-test-1",
            "symbol": "REHYD_SHORT",
            "exchange": "NSE",
            "direction": "SHORT",
            "quantity": 20,
            "entry_price": 500.0,
            "fees_paid": 25.0,
            "product": "MIS",
            "segment": "EQ",
        }]
        restored = await broker.rehydrate_positions(db_rows)
        assert restored == 1
        expected_capital = round(100000.0 - (20 * 500.0 + 25.0), 2)
        assert round(broker.capital, 2) == expected_capital
        margin = await broker.get_margin()
        assert margin["used"] == 10000.0
        assert margin["available"] == expected_capital
