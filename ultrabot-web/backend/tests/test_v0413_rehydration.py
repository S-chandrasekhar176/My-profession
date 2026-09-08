"""v0.4.13 rehydration fix tests.

Live incident 2026-09-07 09:42 IST: same-day engine restart left the
paper broker's in-memory book empty while the DB held 3 open positions —
the next save_state() then persisted 0 positions and destroyed the
session snapshot. Covers:
- PaperBroker.rehydrate_positions: capital math mirrors the original
  BUY/SELL leg, direction mapping (BUY/SELL + LONG/SHORT accepted),
  existing-open dedupe, invalid-row tolerance
- SessionManager.save_state: DB-truth merge when the broker book is empty
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from brokers.paper_broker import PaperBroker
from core.session_manager import SessionManager


def _row(symbol: str, direction: str, qty: int, entry: float, fees: float = 5.0) -> Dict[str, Any]:
    return {
        "id": f"pos-{symbol}",
        "symbol": symbol,
        "exchange": "NSE",
        "direction": direction,
        "quantity": qty,
        "entry_price": entry,
        "current_price": entry,
        "invested_amount": entry * qty,
        "entry_time": "2026-09-08T10:00:00+05:30",
        "product": "MIS",
        "segment": "EQ",
        "fees_paid": fees,
    }


# ── PaperBroker.rehydrate_positions ─────────────────────────────────

@pytest.mark.asyncio
async def test_rehydrate_long_mirrors_buy_capital_movement():
    broker = PaperBroker(initial_capital=100000.0)
    n = await broker.rehydrate_positions([_row("SUPREMEIND", "BUY", 8, 3570.89, 57.58)])
    assert n == 1
    pos = broker.positions["SUPREMEIND"]
    assert pos["status"] == "OPEN"
    assert pos["direction"] == "LONG"
    assert pos["quantity"] == 8
    assert pos["entry_price"] == 3570.89
    assert pos["invested_amount"] == round(3570.89 * 8, 2)
    # BUY leg: capital -= invested + entry fees
    assert broker.capital == pytest.approx(100000.0 - (3570.89 * 8 + 57.58))
    open_list = await broker.get_positions()
    assert len(open_list) == 1


@pytest.mark.asyncio
async def test_rehydrate_short_mirrors_sell_capital_movement():
    broker = PaperBroker(initial_capital=100000.0)
    n = await broker.rehydrate_positions([_row("BDL", "SELL", 31, 1261.87, 61.42)])
    assert n == 1
    pos = broker.positions["BDL"]
    assert pos["direction"] == "SHORT"
    # SELL leg: capital += invested - entry fees
    assert broker.capital == pytest.approx(100000.0 + (1261.87 * 31 - 61.42))


@pytest.mark.asyncio
async def test_rehydrate_accepts_long_short_words():
    broker = PaperBroker(initial_capital=50000.0)
    n = await broker.rehydrate_positions(
        [_row("A", "LONG", 1, 100.0), _row("B", "SHORT", 1, 100.0)]
    )
    assert n == 2
    assert broker.positions["A"]["direction"] == "LONG"
    assert broker.positions["B"]["direction"] == "SHORT"


@pytest.mark.asyncio
async def test_rehydrate_skips_already_open_symbol():
    broker = PaperBroker(initial_capital=100000.0)
    await broker.place_order("ZZ", transaction_type="BUY", quantity=10, price=50.0)
    cap_after_live_open = broker.capital
    n = await broker.rehydrate_positions([_row("ZZ", "BUY", 99, 1.0)])
    assert n == 0
    assert broker.positions["ZZ"]["quantity"] == 10  # untouched
    assert broker.capital == cap_after_live_open


@pytest.mark.asyncio
async def test_rehydrate_tolerates_invalid_rows():
    broker = PaperBroker(initial_capital=10000.0)
    rows = [
        {"symbol": "", "quantity": 5, "entry_price": 10.0},
        {"symbol": "X", "quantity": 0, "entry_price": 10.0},
        {"symbol": "Y", "quantity": 5, "entry_price": 0.0},
    ]
    assert await broker.rehydrate_positions(rows) == 0
    assert broker.capital == 10000.0


@pytest.mark.asyncio
async def test_rehydrated_position_closes_cleanly():
    """A rehydrated LONG must close with the same capital delta as a
    live-opened one (end-to-end accounting consistency)."""
    cap0 = 100000.0
    live = PaperBroker(initial_capital=cap0)
    await live.place_order("AA", transaction_type="BUY", quantity=10, price=100.0)

    rehyd = PaperBroker(initial_capital=cap0)
    await rehyd.rehydrate_positions([_row("AA", "BUY", 10, 100.0, fees=live.orders[
        [o for o in live.orders][0]]["fees"])])

    assert live.capital == pytest.approx(rehyd.capital)

    r1 = await live.close_position("AA", exit_price=105.0)
    r2 = await rehyd.close_position("AA", exit_price=105.0)
    assert r1["net_pnl"] == r2["net_pnl"]
    assert live.capital == pytest.approx(rehyd.capital)


# ── SessionManager.save_state DB-truth merge ─────────────────────────

class _FakeDBPosition:
    def __init__(self, symbol: str, qty: int, entry: float):
        self.symbol = symbol
        self.quantity = qty
        self.entry_price = entry


class _FakeRepo:
    def __init__(self, open_positions: List[_FakeDBPosition]):
        self._open = open_positions
        self.saved_state: Dict[str, Any] = {}

    async def get_active_watchlist(self):
        return []

    async def get_open_positions(self):
        return self._open

    async def save_session_state(self, session_id, state):
        self.saved_state = dict(state)


class _EmptyBookBroker:
    """Post-restart paper broker: fresh process, empty book."""

    async def get_positions(self):
        return []


class _MinimalEngine:
    """Duck-typed engine: only what save_state touches."""

    def __init__(self):
        self.broker = _EmptyBookBroker()
        self.session_id = "sess-1"
        self.mode = "paper"


@pytest.mark.asyncio
async def test_save_state_merges_db_open_positions_into_empty_book():
    fake_repo = _FakeRepo([_FakeDBPosition("SUPREMEIND", 8, 3570.89)])
    sm = SessionManager(lambda: fake_repo)
    await sm.save_state("sess-1", _MinimalEngine())
    state = fake_repo.saved_state
    syms = [p["symbol"] for p in state["open_positions"]]
    assert syms == ["SUPREMEIND"]
    assert state["open_positions"][0]["quantity"] == 8
    assert state["open_positions"][0]["avg_price"] == 3570.89


@pytest.mark.asyncio
async def test_save_state_does_not_duplicate_broker_positions():
    class _FullBookBroker:
        async def get_positions(self):
            return [{"symbol": "SUPREMEIND", "quantity": 8, "avg_price": 3570.89, "pnl": 1.0}]

    eng = _MinimalEngine()
    eng.broker = _FullBookBroker()
    fake_repo = _FakeRepo([_FakeDBPosition("SUPREMEIND", 8, 3570.89)])
    sm = SessionManager(lambda: fake_repo)
    await sm.save_state("sess-1", eng)
    assert len(fake_repo.saved_state["open_positions"]) == 1


@pytest.mark.asyncio
async def test_save_state_empty_everything_is_safe():
    fake_repo = _FakeRepo([])
    sm = SessionManager(lambda: fake_repo)
    await sm.save_state("sess-1", _MinimalEngine())
    assert fake_repo.saved_state["open_positions"] == []
