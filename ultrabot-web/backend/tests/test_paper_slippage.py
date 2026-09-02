"""Tests for the paper-fill slippage model (P2-b).

* fees/slippage.estimate_slippage_bps — disabled/enabled/impact/cap/degenerate
* apply_slippage — BUY pays up, SELL sells down, amounts, safety
* PaperBroker — default construction keeps exact fills (backward compat),
  enabled config slips MARKET orders only (LIMIT untouched), order dict
  reports slippage transparency fields, capital math uses slipped price
* BrokerFactory — production wiring pulls the config from defaults.yaml;
  explicit kwargs override
"""

import pytest

from brokers.paper_broker import PaperBroker
from fees.slippage import apply_slippage, estimate_slippage_bps

_CFG = {"enabled": True, "base_bps": 5.0, "impact_bps_per_crore": 2.0, "max_bps": 25.0}


# ─────────────────────────────────────────────
# estimate_slippage_bps
# ─────────────────────────────────────────────


def test_disabled_config_is_zero():
    assert estimate_slippage_bps(500000, {"enabled": False}) == 0.0
    assert estimate_slippage_bps(500000, {}) == 0.0
    assert estimate_slippage_bps(500000, None) == 0.0


def test_base_bps_applies_to_small_orders():
    # ₹5L order: impact = 2 × 0.05 = 0.1 bps → 5.1
    assert estimate_slippage_bps(500_000, _CFG) == pytest.approx(5.1)


def test_impact_scales_with_order_value():
    one_cr = estimate_slippage_bps(1e7, _CFG)      # 5 + 2 = 7
    five_cr = estimate_slippage_bps(5e7, _CFG)     # 5 + 10 = 15
    assert one_cr == pytest.approx(7.0)
    assert five_cr == pytest.approx(15.0)


def test_max_bps_caps_pathological_sizes():
    assert estimate_slippage_bps(1e9, _CFG) == 25.0   # would be 205 → capped
    assert estimate_slippage_bps(1e12, _CFG) == 25.0


def test_zero_or_negative_order_value_is_zero():
    assert estimate_slippage_bps(0, _CFG) == 0.0
    assert estimate_slippage_bps(-100, _CFG) == 0.0


def test_malformed_config_never_breaks():
    assert estimate_slippage_bps(1000, {"enabled": True, "base_bps": "abc"}) == 0.0
    # truthy string counts as enabled; tiny order → base + negligible impact
    assert estimate_slippage_bps(1000, {"enabled": "yes", "base_bps": 5}) == pytest.approx(5.0002)


# ─────────────────────────────────────────────
# apply_slippage
# ─────────────────────────────────────────────


def test_buy_pays_up_sell_sells_down():
    buy_fill, bps, amt = apply_slippage(1000.0, is_buy=True, quantity=10, config=_CFG)
    # ₹10,000 order: 5 + 2×(1e4/1e7) = 5.002 bps
    assert bps == pytest.approx(5.002)
    assert buy_fill == pytest.approx(1000.0 * (1 + 5.002 / 10000), rel=1e-4)
    assert amt == pytest.approx(round((buy_fill - 1000.0) * 10, 2))

    sell_fill, bps2, amt2 = apply_slippage(1000.0, is_buy=False, quantity=10, config=_CFG)
    assert sell_fill < 1000.0
    assert sell_fill == pytest.approx(1000.0 * (1 - 5.002 / 10000), rel=1e-4)


def test_apply_slippage_disabled_is_exact():
    fill, bps, amt = apply_slippage(1234.56, is_buy=True, quantity=7, config={"enabled": False})
    assert fill == 1234.56
    assert bps == 0.0
    assert amt == 0.0


def test_apply_slippage_invalid_inputs_safe():
    assert apply_slippage(0, True, 10, _CFG) == (0.0, 0.0, 0.0)
    assert apply_slippage(1000.0, True, 0, _CFG)[1] == 0.0
    assert apply_slippage(-5, True, 10, _CFG) == (-5.0, 0.0, 0.0)


# ─────────────────────────────────────────────
# PaperBroker integration
# ─────────────────────────────────────────────


class _FixedFeed:
    """Feed stub that always quotes the same LTP."""

    def __init__(self, price):
        self._p = price

    async def get_ltp(self, symbol):
        return self._p


@pytest.mark.asyncio
async def test_default_construction_keeps_exact_fills():
    """Backward compat: brokers built without a slippage config (tests,
    direct constructions) fill at the exact reference price."""
    broker = PaperBroker(initial_capital=1_000_000)
    broker.feed = _FixedFeed(1000.0)

    res = await broker.place_order(
        symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=10, price=0.0, order_type="MARKET",
    )
    assert res["success"] is True
    assert res["filled_price"] == 1000.0
    assert res["slippage_bps"] == 0.0


@pytest.mark.asyncio
async def test_market_buy_fills_above_ltp_with_slippage():
    broker = PaperBroker(initial_capital=1_000_000, slippage_config=dict(_CFG))
    broker.feed = _FixedFeed(1000.0)

    res = await broker.place_order(
        symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=10, price=0.0, order_type="MARKET",
    )
    assert res["success"] is True
    assert res["filled_price"] > 1000.0
    assert res["slippage_bps"] == pytest.approx(5.0)  # round(5.002, 2)
    assert res["slippage_amount"] > 0

    # Capital math used the slipped price (fees aside)
    expected_cost = res["filled_price"] * 10 + res["fees"]
    assert broker.capital == pytest.approx(1_000_000 - expected_cost, rel=1e-6)


@pytest.mark.asyncio
async def test_market_sell_fills_below_ltp():
    broker = PaperBroker(initial_capital=1_000_000, slippage_config=dict(_CFG))
    broker.feed = _FixedFeed(1000.0)

    res = await broker.place_order(
        symbol="RELIANCE", exchange="NSE", transaction_type="SELL",
        quantity=10, price=0.0, order_type="MARKET",
    )
    assert res["success"] is True
    assert res["filled_price"] < 1000.0


@pytest.mark.asyncio
async def test_limit_orders_do_not_slip():
    """A LIMIT order fills at its limit price by definition."""
    broker = PaperBroker(initial_capital=1_000_000, slippage_config=dict(_CFG))
    broker.feed = _FixedFeed(1000.0)

    res = await broker.place_order(
        symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=10, price=995.0, order_type="LIMIT",
    )
    assert res["success"] is True
    assert res["filled_price"] == 995.0
    assert res["slippage_bps"] == 0.0


@pytest.mark.asyncio
async def test_large_order_impact_visible_in_fill():
    """₹1Cr order → 7 bps slipped on the BUY entry."""
    broker = PaperBroker(initial_capital=20_000_000, slippage_config=dict(_CFG))
    broker.feed = _FixedFeed(1000.0)

    res = await broker.place_order(
        symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
        quantity=10_000, price=0.0, order_type="MARKET",  # ₹1Cr
    )
    assert res["success"] is True
    assert res["slippage_bps"] == pytest.approx(7.0)
    assert res["filled_price"] == pytest.approx(1000.70, rel=1e-3)


@pytest.mark.asyncio
async def test_round_trip_slippage_drag_is_reported():
    """Entry + exit both slip — total drag is visible and roughly 2× the
    one-way cost for a small order."""
    broker = PaperBroker(initial_capital=1_000_000, slippage_config=dict(_CFG))
    broker.feed = _FixedFeed(1000.0)

    entry = await broker.place_order(
        symbol="RELIANCE", transaction_type="BUY", quantity=10,
        price=0.0, order_type="MARKET",
    )
    exit_ = await broker.place_order(
        symbol="RELIANCE", transaction_type="SELL", quantity=10,
        price=0.0, order_type="MARKET",
    )
    total_drag = entry["slippage_amount"] + exit_["slippage_amount"]
    # ~5.002 bps each way on ₹10,000 → ~₹10.0 round trip
    assert total_drag == pytest.approx(2 * 1000.0 * 10 * 5.002 / 10000, rel=0.05)


# ─────────────────────────────────────────────
# Factory wiring
# ─────────────────────────────────────────────


def test_factory_reads_slippage_from_settings():
    from brokers.factory import BrokerFactory

    broker = BrokerFactory.create("paper", mode="paper", initial_capital=500_000)
    assert isinstance(broker, PaperBroker)
    # defaults.yaml ships slippage enabled — production paper fills are realistic
    assert broker.slippage_config.get("enabled") is True
    assert broker.slippage_config.get("base_bps") == 5.0


def test_factory_explicit_config_overrides_settings():
    from brokers.factory import BrokerFactory

    broker = BrokerFactory.create(
        "paper", mode="paper", initial_capital=500_000,
        slippage_config={"enabled": False},
    )
    assert broker.slippage_config == {"enabled": False}
