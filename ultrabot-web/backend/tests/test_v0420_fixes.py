"""v0.4.20 regression tests.

Covers the wed_v0.4.20 fix wave:
  1. CP-02 (over-exit quantity): a close that reuses a position object whose
     in-memory quantity is stale (original) must NOT place an exit order for
     the booked tranche — partial_complete (close_qty=0) places NO order,
     and explicit close_qty is honored for the order quantity.
  2. CP-04 (double-counted partial P&L): the final leg's ledger P&L is
     computed on the EFFECTIVE close qty; with close_qty=0 the round trip is
     the partial legs alone and no phantom ₹40 exit brokerage is charged.
  3. Expired-opportunities list: /api/opportunities/invalidated now MERGES
     engine memory with today's DB signals (status=EXPIRED) so the list
     survives engine restarts (user report: expired tab stayed empty).
  4. CP-05a: the backtester's 20-MA crossover fallback is gone (honest
     NO_TRADE when the strategy is silent).
  5. CP-05b: backtester partial booking banks real legs (PARTIAL_L rows,
     remaining_qty shrink) instead of SL-tighten-only decoration.
  6. Frontend contract: the WS opportunity_invalidated handler persists the
     expired id + reason to localStorage (instant Expired-tab marking).
"""
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

ENGINE_SRC = (BACKEND / "core" / "engine.py").read_text()
BACKTEST_SRC = (BACKEND / "api" / "routes" / "backtest.py").read_text()
OPP_API_SRC = (BACKEND / "api" / "routes" / "opportunities.py").read_text()
WS_TS_SRC = (BACKEND.parents[1] / "src" / "hooks" / "useWebSocket.ts").read_text()


def _make_pos(direction="LONG", entry=100.0, current=105.0, qty=76, extra=None):
    return SimpleNamespace(
        id="pos-1",
        trade_id="trade-1",
        symbol="HDFCLIFE",
        strategy="ORB",
        direction=direction,
        quantity=qty,
        entry_price=entry,
        current_price=current,
        stop_loss=95.0,
        target=110.0,
        entry_time="2026-09-10T10:00:00+05:30",
        extra=extra,
    )


def _make_close_engine(broker=None):
    """Same proven pattern as test_v048_p0_fixes.py / squareoff direction fix."""
    from core.engine import UltraBotEngine

    engine = MagicMock(spec=UltraBotEngine)
    engine.broker = broker
    engine.session_id = "test-session"
    engine._errors_count = 0
    engine.config = MagicMock()
    engine.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    engine.daily_risk = None
    engine.error_engine = MagicMock()
    engine.error_engine.handle_error = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()

    repo = MagicMock()
    repo.update_position = AsyncMock()
    repo.update_trade = AsyncMock()

    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    engine._repo_context = MagicMock(return_value=RepoCtx())
    engine._position_extra_dict = UltraBotEngine._position_extra_dict
    engine._close_position = UltraBotEngine._close_position.__get__(
        engine, UltraBotEngine
    )
    return engine, repo


def _trade_update_kwargs(repo):
    _, kwargs = repo.update_trade.await_args
    return kwargs


# ────────────────────────────────────────────────
# 1+2. CP-02 / CP-04 — close quantity semantics
# ────────────────────────────────────────────────

class TestCloseQtySemantics:
    @pytest.mark.asyncio
    async def test_partial_complete_places_no_exit_order(self):
        """close_qty=0 (stage exhaustion) must NOT place any exit order —
        the pre-fix code exited the ORIGINAL quantity (CP-02) and charged a
        phantom ₹40 round-trip brokerage on zero shares (CP-04 fee leg)."""
        broker = AsyncMock()
        broker.place_order = AsyncMock(
            return_value={"status": "FILLED", "filled_price": 103.0}
        )
        engine, repo = _make_close_engine(broker=broker)
        pos = _make_pos(qty=76)  # stale in-memory original qty
        extra = {"partial_realized_pnl": 150.0, "partial_fees": 45.0}
        pos.extra = json.dumps(extra)

        await engine._close_position(
            position=pos, exit_price=103.0, close_reason="partial_complete",
            pnl_amount=0, pnl_pct=0, close_qty=0,
        )

        broker.place_order.assert_not_awaited()

        kwargs = _trade_update_kwargs(repo)
        # Round trip = partial legs only: gross 150, fees 45, net 105
        assert kwargs["pnl"] == pytest.approx(150.0)
        assert kwargs["fees"] == pytest.approx(45.0)
        assert kwargs["net_pnl"] == pytest.approx(105.0)
        assert kwargs["status"] == "CLOSED"

    @pytest.mark.asyncio
    async def test_final_close_uses_explicit_remaining_qty(self):
        """A close after partial booking must exit (and compute P&L on) the
        REMAINING tranche — not the stale original quantity."""
        broker = AsyncMock()
        broker.place_order = AsyncMock(
            return_value={"status": "FILLED", "filled_price": 101.0}
        )
        engine, repo = _make_close_engine(broker=broker)
        pos = _make_pos(direction="LONG", entry=100.0, qty=76)  # stale 76
        pos.extra = json.dumps({"partial_realized_pnl": 38.0, "partial_fees": 42.0})

        await engine._close_position(
            position=pos, exit_price=101.0, close_reason="stop_loss",
            pnl_amount=0, pnl_pct=0, close_qty=57,
        )

        # Exit order: exactly the remaining 57 shares, never 76 (CP-02)
        assert broker.place_order.await_count == 1
        _, okwargs = broker.place_order.await_args
        assert int(okwargs["quantity"]) == 57

        # Final leg P&L on 57 shares: (101-100)*57 = 57 gross
        kwargs = _trade_update_kwargs(repo)
        assert kwargs["pnl"] == pytest.approx(57.0 + 38.0)   # + partial gross
        # fees = final-leg round trip (incl ₹40) + partial_fees 42; net = pnl - fees
        assert kwargs["fees"] == pytest.approx(42.0 + 49.29, abs=1.0)
        assert kwargs["net_pnl"] == pytest.approx(kwargs["pnl"] - kwargs["fees"])

    @pytest.mark.asyncio
    async def test_default_close_qty_falls_back_to_position_quantity(self):
        """No close_qty passed (all existing callers) → position.quantity —
        back-compat for fresh post-v0.4.18 objects (quantity == remaining)."""
        broker = AsyncMock()
        broker.place_order = AsyncMock(
            return_value={"status": "FILLED", "filled_price": 104.0}
        )
        engine, repo = _make_close_engine(broker=broker)
        pos = _make_pos(direction="LONG", entry=100.0, qty=31)

        await engine._close_position(
            position=pos, exit_price=104.0, close_reason="target",
            pnl_amount=0, pnl_pct=0,
        )

        _, okwargs = broker.place_order.await_args
        assert int(okwargs["quantity"]) == 31
        kwargs = _trade_update_kwargs(repo)
        assert kwargs["pnl"] == pytest.approx((104.0 - 100.0) * 31)

    def test_partial_booking_syncs_in_memory_quantity(self):
        """CP-02 root cause: after booking, the in-memory object's quantity
        must be shrunk alongside the DB row (source-level contract)."""
        assert "position.quantity = int(remaining_qty)" in ENGINE_SRC
        assert "position.remaining_qty = int(remaining_qty)" in ENGINE_SRC


# ────────────────────────────────────────────────
# 3. Expired-opportunities API — restart-durable merge
# ────────────────────────────────────────────────

class _FakeSignal:
    def __init__(self, sid, symbol, status, reason=None, created="2026-09-10T09:30:00+05:30", updated=None):
        self.id = sid
        self.symbol = symbol
        self.direction = "LONG"
        self.strategy = "ORB"
        self.status = status
        self.confidence = 0.7
        self.entry_price = 100.0
        self.stop_loss = 98.5
        self.target = 103.0
        self.rejection_reason = reason
        self.created_at = created
        self.updated_at = updated or created
        self.signal_data = json.dumps(
            {"opportunity": {"id": f"opp-{sid}", "symbol": symbol}}
        )


class TestInvalidatedApiMerge:
    @pytest.mark.asyncio
    async def test_merges_engine_memory_with_db_expired_signals(self):
        """The expired list must include today's DB EXPIRED signals even when
        engine.invalidated_opportunities was wiped by a restart."""
        import importlib

        mod = importlib.import_module("api.routes.opportunities")

        engine = SimpleNamespace(
            invalidated_opportunities={
                "opp-live-1": {"id": "opp-live-1", "symbol": "LIVE", "status": "expired",
                               "invalidation_reason": "TTL", "invalidated_at": "2026-09-10T10:00:00+05:30"},
            }
        )

        async def _get_repo():
            return None

        rows = [
            _FakeSignal("s1", "AAA", "EXPIRED", reason="TTL elapsed", updated="2026-09-10T09:35:00+05:30"),
            _FakeSignal("s2", "BBB", "EXPIRED", reason="Target gone"),
            _FakeSignal("s3", "CCC", "ACCEPTED"),          # must be excluded
            _FakeSignal("s4", "DDD", "REJECTED"),          # must be excluded
        ]

        class Repo:
            async def get_todays_signals(self):
                return rows

        @asynccontextmanager
        async def repo_ctx():
            yield Repo()

        engine._repo_context = repo_ctx

        out = await mod.get_invalidated_opportunities(username="u", engine=engine)

        ids = [item["id"] for item in out]
        assert "opp-live-1" in ids            # engine memory source
        assert "opp-s1" in ids                # DB source (signal_data snapshot id)
        assert "opp-s2" in ids
        assert "opp-s3" not in ids            # non-EXPIRED rows excluded
        assert "opp-s4" not in ids

        s1 = next(i for i in out if i["id"] == "opp-s1")
        assert s1["status"] == "expired"
        assert s1["direction"] == "BUY"       # LONG -> BUY for the UI
        assert s1["invalidation_reason"] == "TTL elapsed"
        assert s1["signal_id"] == "s1"

        # Newest first
        times = [str(i.get("invalidated_at") or "") for i in out]
        assert times == sorted(times, reverse=True)

    @pytest.mark.asyncio
    async def test_engine_none_returns_empty_not_crash(self):
        import importlib

        mod = importlib.import_module("api.routes.opportunities")
        out = await mod.get_invalidated_opportunities(username="u", engine=None)
        assert out == []


# ────────────────────────────────────────────────
# 4+5. Backtest honesty (CP-05a / CP-05b)
# ────────────────────────────────────────────────

class TestBacktestHonesty:
    def test_ma_crossover_fallback_removed(self):
        """CP-05a: no fabricated MA-cross trades attributed to strategies."""
        assert "Fallback to MA momentum breakout" not in BACKTEST_SRC
        assert "curr_c > ma20" not in BACKTEST_SRC
        assert "0.65" not in BACKTEST_SRC.split("def ")[0] or "MA crossover FALLBACK is REMOVED" in BACKTEST_SRC

    def test_partial_booking_banks_real_legs(self):
        """CP-05b: booked tranches shrink remaining_qty and are logged."""
        assert "PARTIAL_L" in BACKTEST_SRC
        assert "remaining_qty -= _bt_book_qty" in BACKTEST_SRC
        assert '"initial_qty": qty' in BACKTEST_SRC

    def test_stage_stub_feeds_booker(self):
        """The enriched stub must drive the real PartialBooker stage math."""
        from risk.partial_booker import PartialBooker

        booker = PartialBooker({})
        stub = SimpleNamespace(
            entry_price=100.0,
            sl_price=98.5,
            stop_loss=98.5,
            direction="LONG",
            quantity=100,
            initial_quantity=100,
            stages_fired=[],
            peak_price=0,
            extra={},
        )
        # +1.2% move: stage 1 (0.5% breakeven lock, 0 qty) + stage 2 (1.0%, 25%)
        res = booker.check_and_book(stub, 101.2)
        assert res.triggered_level in (1, 2)
        if res.triggered_level == 2:
            assert int(res.book_qty) == 25


# ────────────────────────────────────────────────
# 6. Frontend WS contract — expired marking
# ────────────────────────────────────────────────

class TestFrontendWsContract:
    def test_invalidated_event_persists_expired_id(self):
        """opportunity_invalidated must save the expired id (+ reason) to
        localStorage, not just remove the pending card."""
        assert "saveStoredExpiredOppId" in WS_TS_SRC
        assert "opportunity_invalidated" in WS_TS_SRC
        # Save must happen INSIDE the invalidation branch, not only removal
        assert WS_TS_SRC.index("opportunity_invalidated") < WS_TS_SRC.index("saveStoredExpiredOppId(String(oppId)")
