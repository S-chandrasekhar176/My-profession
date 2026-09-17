"""Comprehensive Broker Order Simulation Audit (Item 2).

Tests:
1. Order Slicing & Exchange Freeze Limits (NSE NIFTY, BANKNIFTY, Equity limits)
2. Live Bid-Ask Spread Crossing & Impact Slippage (Fees / Slippage model)
3. Limit vs Market Order Friction Discrepancy (Limit has 0 slippage)
4. Capital & Margin Accounting (MIS Short Sale 20% margin lock)
"""
import pytest
from brokers.paper_broker import PaperBroker
from fees.slippage import apply_slippage, estimate_slippage_bps
from utils.order_slicer import slice_order, get_freeze_limit, FREEZE_LIMITS


class _MockFeed:
    def __init__(self, price: float):
        self.price = price

    async def get_ltp(self, symbol: str, exchange: str = "NSE") -> float:
        return self.price


# ─────────────────────────────────────────────
# 1. Order Slicing & Freeze Limits Tests
# ─────────────────────────────────────────────

def test_get_freeze_limit_indices():
    assert get_freeze_limit("NIFTY24SEP25000CE", segment="FNO") == 1800
    assert get_freeze_limit("BANKNIFTY24SEP52000PE", segment="FNO") == 900
    assert get_freeze_limit("FINNIFTY", segment="FNO") == 1800
    assert get_freeze_limit("RELIANCE", segment="EQ") == 25000


def test_order_slicing_under_freeze_limit():
    slices = slice_order("NIFTY24SEP25000CE", quantity=1500, price=150.0, segment="FNO")
    assert len(slices) == 1
    assert slices[0]["is_sliced"] is False
    assert slices[0]["quantity"] == 1500


def test_order_slicing_exceeding_freeze_limit():
    # 4500 NIFTY contracts -> freeze limit 1800 -> 3 slices: 1800, 1800, 900
    slices = slice_order("NIFTY24SEP25000CE", quantity=4500, price=150.0, segment="FNO")
    assert len(slices) == 3
    assert slices[0]["quantity"] == 1800
    assert slices[1]["quantity"] == 1800
    assert slices[2]["quantity"] == 900
    assert all(s["is_sliced"] is True for s in slices)
    assert sum(s["quantity"] for s in slices) == 4500


def test_order_slicing_banknifty():
    # 2500 BANKNIFTY contracts -> freeze limit 900 -> 3 slices: 900, 900, 700
    slices = slice_order("BANKNIFTY24SEP52000PE", quantity=2500, price=300.0, segment="FNO")
    assert len(slices) == 3
    assert slices[0]["quantity"] == 900
    assert slices[1]["quantity"] == 900
    assert slices[2]["quantity"] == 700
    assert sum(s["quantity"] for s in slices) == 2500


def test_order_slicing_equity_value_cap():
    # High-priced stock: ₹50,000 / share.
    # Max value = ₹2 Crore -> max single slice = 400 shares.
    slices = slice_order("HIGHVAL", quantity=1000, price=50000.0, segment="EQ")
    assert len(slices) >= 3
    assert sum(s["quantity"] for s in slices) == 1000
    assert all(s["quantity"] <= 400 for s in slices)


# ─────────────────────────────────────────────
# 2. PaperBroker Order Slicing & Slippage Audit
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_paper_broker_slices_large_order():
    broker = PaperBroker(initial_capital=50_000_000.0)
    broker.feed = _MockFeed(200.0)

    # Place order for 3600 NIFTY contracts (freeze limit 1800)
    res = await broker.place_order(
        symbol="NIFTY24SEP25000CE",
        transaction_type="BUY",
        quantity=3600,
        price=200.0,
        order_type="MARKET",
        segment="FNO",
    )
    assert res["status"] == "FILLED"
    order = broker.orders[res["order_id"]]
    assert order["is_sliced"] is True
    assert len(order["slices"]) == 2
    assert order["slices"][0]["quantity"] == 1800
    assert order["slices"][1]["quantity"] == 1800


@pytest.mark.asyncio
async def test_paper_broker_slippage_audit():
    # Slippage enabled: base 5 bps (0.05%)
    broker = PaperBroker(
        initial_capital=10_000_000.0,
        slippage_config={"enabled": True, "base_bps": 5.0, "impact_bps_per_crore": 2.0, "max_bps": 25.0},
    )
    broker.feed = _MockFeed(1000.0)

    # 1. Market BUY order pays UP across spread
    res_buy = await broker.place_order(
        symbol="RELIANCE",
        transaction_type="BUY",
        quantity=100,
        price=1000.0,
        order_type="MARKET",
    )
    order_buy = broker.orders[res_buy["order_id"]]
    assert order_buy["filled_price"] > 1000.0
    assert order_buy["slippage_bps"] >= 5.0
    assert order_buy["slippage_amount"] > 0

    # 2. Market SELL order sells DOWN across spread
    res_sell = await broker.place_order(
        symbol="TCS",
        transaction_type="SELL",
        quantity=100,
        price=1000.0,
        order_type="MARKET",
    )
    order_sell = broker.orders[res_sell["order_id"]]
    assert order_sell["filled_price"] < 1000.0
    assert order_sell["slippage_bps"] >= 5.0

    # 3. Limit order fills at limit price with ZERO slippage
    res_limit = await broker.place_order(
        symbol="INFY",
        transaction_type="BUY",
        quantity=100,
        price=1500.0,
        order_type="LIMIT",
    )
    order_limit = broker.orders[res_limit["order_id"]]
    assert order_limit["filled_price"] == 1500.0
    assert order_limit["slippage_bps"] == 0.0
    assert order_limit["slippage_amount"] == 0.0


@pytest.mark.asyncio
async def test_paper_broker_short_margin_accounting():
    """CP-07 Audit: Verify short sales lock margin and do NOT inflate available cash."""
    initial_cap = 500_000.0
    broker = PaperBroker(initial_capital=initial_cap)
    broker.feed = _MockFeed(1000.0)

    # Sell 100 shares short @ 1000 = 100,000 value
    res = await broker.place_order(
        symbol="SBIN",
        transaction_type="SELL",
        quantity=100,
        price=1000.0,
        order_type="MARKET",
    )
    assert res["status"] == "FILLED"
    
    # Capital must be reduced (margin locked + fees), NOT credited!
    assert broker.capital < initial_cap
    margin = await broker.get_margin()
    assert margin["available"] < initial_cap
