"""v0.4.4 audit-round-2 regression tests: direction bugs OUTSIDE engine.py.

Three defects found during the post-v0.4.3 verification round, all the same
failure class as the original live-run-2 BUY/SELL-vs-LONG/SHORT inversion:

  1. core/scheduler.py on_auto_squareoff (15:20 IST) precomputed square-off
     P&L with a raw ``pos.direction == "LONG"`` — INVERTED for every BUY
     position (a +₹50 gain was passed to _close_position as −₹50, feeding
     the daily-risk circuit breaker a fake loss). pnl_pct was never
     recomputed downstream, so inverted % flowed into the trade-closed
     broadcast and performance tracker even when the fill price masked the
     pnl_amount inversion.
  2. api/routes/trades.py manual-close endpoint passed ``exit_reason=`` to
     engine._close_position whose parameter is ``close_reason`` — TypeError
     → HTTP 500 whenever the engine was running; when it did work (engine
     stopped) the fallback recorded fees on a fixed ₹40 guess.
  3. api/routes/dashboard.py engine-down fallback computed unrealized P&L
     with a raw ``pos.direction == "LONG"`` — inverted for BUY positions.

Hardening shipped alongside: engine._close_position now ALWAYS recomputes
pnl_amount AND pnl_pct from the position's own direction and the effective
(fill) price — caller-supplied P&L is accepted but never trusted.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine import UltraBotEngine
from core.scheduler import MarketLifecycleScheduler
from utils.direction import is_long_direction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_pos(direction, entry=100.0, current=105.0, qty=10):
    return SimpleNamespace(
        id="pos-1",
        trade_id="trade-1",
        symbol="TCS",
        strategy="ORB",
        direction=direction,
        quantity=qty,
        entry_price=entry,
        current_price=current,
        stop_loss=95.0,
        target=110.0,
        entry_time="2026-09-01T10:00:00+05:30",
        extra=None,
    )


def _make_close_engine(broker=None):
    """MagicMock engine with the REAL _close_position bound (proven pattern
    from test_order_direction_real_broker)."""
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
    repo.get_trade = AsyncMock(return_value=None)
    repo.get_trade_by_position = AsyncMock(return_value=None)

    class RepoCtx:
        async def __aenter__(self):
            return repo

        async def __aexit__(self, exc_type, exc, tb):
            pass

    engine._repo_context = MagicMock(return_value=RepoCtx())
    # v0.4.8: bind the REAL extra parser — MagicMock.__float__ returns 1.0,
    # which would fabricate a phantom partial-booking leg (+₹1.00) in the
    # close-path merge under test.
    engine._position_extra_dict = UltraBotEngine._position_extra_dict
    engine._close_position = UltraBotEngine._close_position.__get__(
        engine, UltraBotEngine
    )
    return engine, repo


def _captured_trade_update(repo):
    """Return the kwargs dict passed to repo.update_trade."""
    assert repo.update_trade.await_count >= 1
    args, kwargs = repo.update_trade.await_args
    # update_trade(trade_id, exit_price=..., ...) — positional trade_id
    return args, kwargs


def _close_broadcast(engine):
    for call in engine._broadcast.await_args_list:
        payload = call.args[1] if len(call.args) > 1 else call.args[0]
        if isinstance(payload, dict) and payload.get("type") == "position_closed":
            return payload
    return None


# ---------------------------------------------------------------------------
# 1. utils.direction.is_long_direction — the shared helper contract
# ---------------------------------------------------------------------------
class TestIsLongDirectionHelper:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("BUY", True), ("SELL", False),
            ("LONG", True), ("SHORT", False),
            ("B", True),
            ("buy", True), ("sell", False), (" Buy ", True),
            (None, False), ("", False), ("garbage", False), (0, False),
        ],
    )
    def test_mapping(self, value, expected):
        assert is_long_direction(value) is expected


# ---------------------------------------------------------------------------
# 2. _close_position ALWAYS recomputes (defense in depth)
# ---------------------------------------------------------------------------
class TestClosePositionAlwaysRecomputes:
    @pytest.mark.asyncio
    async def test_buy_position_caller_inverted_pnl_is_overridden(self):
        """The exact scheduler bug: caller passes −50 on a +50 BUY position;
        the recorded trade must still show +50 / +5%."""
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos("BUY", entry=100.0, current=105.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=105.0, close_reason="auto_squareoff",
            pnl_amount=-50.0, pnl_pct=-5.0,
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(50.0)
        assert kwargs["net_pnl"] == pytest.approx(50.0 - kwargs["fees"])
        payload = _close_broadcast(engine)
        assert payload is not None
        assert payload["pnl"] == pytest.approx(50.0)
        assert payload["pnl_pct"] == pytest.approx(5.0)

    @pytest.mark.asyncio
    async def test_sell_position_caller_inverted_pnl_is_overridden(self):
        """SELL entry=100 exit=95 → true +50; caller passes −50."""
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos("SELL", entry=100.0, current=95.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=95.0, close_reason="auto_squareoff",
            pnl_amount=-50.0, pnl_pct=-5.0,
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(50.0)
        payload = _close_broadcast(engine)
        assert payload["pnl_pct"] == pytest.approx(5.0)

    @pytest.mark.parametrize(
        "direction,exit_price,expected",
        [
            ("BUY", 105.0, 50.0), ("LONG", 105.0, 50.0),
            ("SELL", 95.0, 50.0), ("SHORT", 95.0, 50.0),
            ("BUY", 95.0, -50.0), ("SELL", 105.0, -50.0),
        ],
    )
    @pytest.mark.asyncio
    async def test_all_direction_vocabularies(self, direction, exit_price, expected):
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos(direction, entry=100.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=exit_price, close_reason="test",
            pnl_amount=0.0, pnl_pct=0.0,  # trades.py path passes nothing
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_manual_close_path_no_pnl_args(self):
        """trades.py calls without pnl args — recompute fills them in."""
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos("BUY", entry=100.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=103.0, close_reason="MANUAL"
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(30.0)
        payload = _close_broadcast(engine)
        assert payload["pnl_pct"] == pytest.approx(3.0)

    @pytest.mark.asyncio
    async def test_real_paper_broker_fill_slippage_uses_fill_price(self):
        """With a real broker, P&L must come from the ACTUAL fill (slippage),
        preserving pre-v0.4.4 behaviour."""
        from brokers.paper_broker import PaperBroker

        broker = PaperBroker(initial_capital=100000.0)
        engine, repo = _make_close_engine(broker=broker)
        pos = _make_pos("BUY", entry=100.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=100.0, close_reason="test",
            pnl_amount=-999.0, pnl_pct=-99.0,
        )
        _, kwargs = _captured_trade_update(repo)
        # A BUY exit fill at ~100 (slippage crosses the spread): pnl ≈ 0,
        # definitely NOT the caller's −999.
        assert kwargs["pnl"] > -900.0
        assert abs(kwargs["pnl"]) < 5.0

    @pytest.mark.asyncio
    async def test_zero_entry_price_does_not_crash(self):
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos("BUY", entry=0.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=100.0, close_reason="test",
            pnl_amount=5.0, pnl_pct=1.0,
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(1000.0)  # (100-0)*10
        payload = _close_broadcast(engine)
        assert payload["pnl_pct"] == 0.0  # cost basis 0 → guarded

    @pytest.mark.asyncio
    async def test_none_direction_treated_as_short(self):
        """Documented conservative default (matches exit-order routing)."""
        engine, repo = _make_close_engine(broker=None)
        pos = _make_pos(None, entry=100.0, qty=10)

        await engine._close_position(
            position=pos, exit_price=95.0, close_reason="test",
        )
        _, kwargs = _captured_trade_update(repo)
        assert kwargs["pnl"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# 3. Scheduler on_auto_squareoff — P&L direction fix
# ---------------------------------------------------------------------------
class _SquareoffCapture:
    """Stub engine capturing _close_position kwargs; feed/broker absent."""

    def __init__(self):
        self.captured = []
        self.feed = None
        self.broker = None
        self._broadcast = AsyncMock()
        self._route_alert = AsyncMock()

    async def _close_position(self, position, exit_price, close_reason,
                              pnl_amount=0, pnl_pct=0):
        self.captured.append(
            dict(position=position, exit_price=exit_price,
                 close_reason=close_reason, pnl_amount=pnl_amount,
                 pnl_pct=pnl_pct)
        )


class _StubRepo:
    def __init__(self, positions):
        self._positions = positions

    async def get_open_positions(self):
        return self._positions

    def close(self):
        return None


class TestSchedulerSquareoffDirection:
    def _make_scheduler(self, positions):
        engine = _SquareoffCapture()
        sched = MarketLifecycleScheduler(engine=engine, repository_getter=None)
        sched._get_repo = AsyncMock(return_value=_StubRepo(positions))
        sched._is_trading_day = MagicMock(return_value=True)
        return sched, engine

    @pytest.mark.asyncio
    async def test_buy_position_profitable_squareoff(self):
        """The money test: BUY entry 100 / LTP 105 / qty 10 must square off
        with +₹50 (the pre-v0.4.4 code passed −₹50)."""
        pos = _make_pos("BUY", entry=100.0, current=105.0, qty=10)
        sched, engine = self._make_scheduler([pos])

        await sched.on_auto_squareoff()

        assert len(engine.captured) == 1
        cap = engine.captured[0]
        assert cap["pnl_amount"] == pytest.approx(50.0)
        assert cap["pnl_pct"] == pytest.approx(5.0)
        assert cap["close_reason"] == "auto_squareoff"
        assert cap["exit_price"] == pytest.approx(105.0)

    @pytest.mark.asyncio
    async def test_sell_position_profitable_squareoff(self):
        pos = _make_pos("SELL", entry=100.0, current=95.0, qty=10)
        sched, engine = self._make_scheduler([pos])

        await sched.on_auto_squareoff()

        cap = engine.captured[0]
        assert cap["pnl_amount"] == pytest.approx(50.0)
        assert cap["pnl_pct"] == pytest.approx(5.0)

    @pytest.mark.parametrize(
        "direction,current,expected",
        [
            ("BUY", 95.0, -50.0), ("SELL", 105.0, -50.0),
            ("LONG", 105.0, 50.0), ("SHORT", 95.0, 50.0),
        ],
    )
    @pytest.mark.asyncio
    async def test_all_vocabularies_and_signs(self, direction, current, expected):
        pos = _make_pos(direction, entry=100.0, current=current, qty=10)
        sched, engine = self._make_scheduler([pos])

        await sched.on_auto_squareoff()

        cap = engine.captured[0]
        assert cap["pnl_amount"] == pytest.approx(expected)

    @pytest.mark.asyncio
    async def test_multiple_positions_mixed_directions(self):
        positions = [
            _make_pos("BUY", entry=100.0, current=110.0, qty=10),   # +100
            _make_pos("SELL", entry=200.0, current=190.0, qty=5),   # +50
            _make_pos("BUY", entry=50.0, current=45.0, qty=20),     # −100
        ]
        sched, engine = self._make_scheduler(positions)

        await sched.on_auto_squareoff()

        assert len(engine.captured) == 3
        pnls = [c["pnl_amount"] for c in engine.captured]
        assert pnls[0] == pytest.approx(100.0)
        assert pnls[1] == pytest.approx(50.0)
        assert pnls[2] == pytest.approx(-100.0)

    @pytest.mark.asyncio
    async def test_not_a_trading_day_skips(self):
        pos = _make_pos("BUY")
        sched, engine = self._make_scheduler([pos])
        sched._is_trading_day = MagicMock(return_value=False)

        await sched.on_auto_squareoff()

        assert engine.captured == []


# ---------------------------------------------------------------------------
# 4. Dashboard engine-down fallback — unrealized P&L direction fix
# ---------------------------------------------------------------------------
class TestDashboardFallbackDirection:
    @pytest.mark.asyncio
    async def _call(self, positions):
        from api.routes.dashboard import get_dashboard

        repo = MagicMock()
        repo.get_open_positions = AsyncMock(return_value=positions)
        repo.get_todays_pnl = AsyncMock(
            return_value={"realized": 0.0, "unrealized": 0.0}
        )
        repo.get_todays_trades = AsyncMock(return_value=[])
        repo.get_watchlist_count = AsyncMock(return_value=0)
        data = await get_dashboard(username="u", engine=None, repo=repo)
        return data

    @pytest.mark.asyncio
    async def test_buy_position_unrealized_not_inverted(self):
        """BUY entry 100 / current 105 / qty 10 → unrealized +50 (the
        pre-v0.4.4 fallback reported −50)."""
        pos = _make_pos("BUY", entry=100.0, current=105.0, qty=10)
        data = await self._call([pos])
        row = data["open_positions"][0]
        assert row["unrealized_pnl"] == pytest.approx(50.0)
        assert row["unrealized_pnl_pct"] == pytest.approx(5.0)
        assert data["capital"]["unrealized_pnl"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_sell_position_unrealized_not_inverted(self):
        pos = _make_pos("SELL", entry=100.0, current=95.0, qty=10)
        data = await self._call([pos])
        row = data["open_positions"][0]
        assert row["unrealized_pnl"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_legacy_long_short_strings(self):
        pos = _make_pos("LONG", entry=100.0, current=105.0, qty=10)
        data = await self._call([pos])
        assert data["open_positions"][0]["unrealized_pnl"] == pytest.approx(50.0)

    @pytest.mark.asyncio
    async def test_losing_buy_not_flipped_into_fake_gain(self):
        pos = _make_pos("BUY", entry=100.0, current=95.0, qty=10)
        data = await self._call([pos])
        assert data["open_positions"][0]["unrealized_pnl"] == pytest.approx(-50.0)


# ---------------------------------------------------------------------------
# 5. Manual-close endpoint call binding (the exit_reason TypeError)
# ---------------------------------------------------------------------------
class TestClosePositionCallSignatureBinding:
    """AST-scan every production caller of _close_position and verify each
    call binds against the REAL method signature — the old ``exit_reason=``
    keyword raised TypeError at runtime and 500'd the endpoint, invisible to
    every mock-free test because the route was never exercised end-to-end.
    """

    def test_all_backend_callers_bind_to_real_signature(self):
        sig = inspect.signature(UltraBotEngine._close_position)
        valid_params = set(sig.parameters.keys()) - {"self"}
        offenders = []

        for path in Path(".").rglob("*.py"):
            s = str(path)
            if "venv" in s or s.startswith("tests/") or "node_modules" in s:
                continue
            try:
                tree = ast.parse(path.read_text())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # match obj._close_position(...) — any receiver
                if not (isinstance(func, ast.Attribute) and func.attr == "_close_position"):
                    continue
                bad_kwargs = [
                    kw.arg for kw in node.keywords
                    if kw.arg is not None and kw.arg not in valid_params
                ]
                if bad_kwargs:
                    offenders.append(f"{s}:{node.lineno} unknown kwargs {bad_kwargs}")

        assert not offenders, (
            "calls to _close_position with kwargs that don't exist in its "
            f"signature (TypeError at runtime): {offenders}"
        )

    def test_trades_route_uses_close_reason_keyword(self):
        """Pin the exact historical bug: the engine-delegation call in
        trades.py must pass close_reason= (repo.update_trade's separate
        exit_reason= kwarg is a different, legitimate API)."""
        tree = ast.parse(Path("api/routes/trades.py").read_text())
        engine_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_close_position"
        ]
        assert engine_calls, "trades.py must delegate to engine._close_position"
        for call in engine_calls:
            kw_names = [kw.arg for kw in call.keywords if kw.arg is not None]
            assert "close_reason" in kw_names, (
                f"engine delegation must pass close_reason= (got {kw_names})"
            )
            assert "exit_reason" not in kw_names, (
                "exit_reason= raised TypeError at runtime (v0.4.4 bug)"
            )
