#!/usr/bin/env python
"""v0.4.4 evidence probe — direction-bug fixes on REAL production wiring.

Run from ultrabot-web/backend with the venv active:
    python evidence/probe_v044_fixes.py

Proves, against the real scheduler / engine / route modules (no mocks of the
code under test):

  P1  15:20 auto-squareoff P&L direction — BUY 100->105 x10 must square off
      at +50 (pre-v0.4.4 the raw `pos.direction == "LONG"` comparison
      recorded -50 and fed the daily-risk circuit breaker a fake loss)
  P2  same for SELL 100->95 x10 -> +50
  P3  BUY losing trade stays negative (-50)
  P4  _close_position defense-in-depth — a caller passing the OLD inverted
      values (-50 / -5%) is overridden: recorded trade pnl +50 / +5%
  P5  manual-close style call (no pnl args) — pnl recomputed (+50), the
      pre-v0.4.4 keyword bug (exit_reason= -> TypeError) no longer raised
  P6  dashboard engine-down fallback unrealized P&L — BUY 100->105 x10
      reports +50 (pre-v0.4.4: -50)
  P7  G16 regression — BUY in a Bear regime still blocked end-to-end
      (proves the shared direction helper refactor touched nothing)
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from datetime import datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

# Make backend imports work when run from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.CRITICAL)

IST = ZoneInfo("Asia/Kolkata")
PASS, FAIL = "PASS", "FAIL"
results = []


def report(pid: str, desc: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"{pid}) [{'PASS' if ok else 'FAIL'}] {desc}" + (f"  -> {detail}" if detail else ""))


# ---------------------------------------------------------------------------
async def probe_scheduler_squareoff() -> None:
    from core.scheduler import MarketLifecycleScheduler

    class CapEngine:
        def __init__(self):
            self.captured = []
            self.feed = None
            self.broker = None

        async def _close_position(self, position, exit_price, close_reason,
                                  pnl_amount=0, pnl_pct=0):
            self.captured.append((pnl_amount, pnl_pct))

        async def _broadcast(self, *a, **k):
            pass

        async def _route_alert(self, *a, **k):
            pass

    class StubRepo:
        def __init__(self, positions):
            self.positions = positions

        async def get_open_positions(self):
            return self.positions

        def close(self):
            return None

    def make_pos(direction, entry, current, qty):
        return SimpleNamespace(
            id="p1", trade_id="t1", symbol="TCS", direction=direction,
            entry_price=entry, current_price=current, quantity=qty,
            stop_loss=0.0, target=0.0,
        )

    async def run(direction, entry, current, qty):
        eng = CapEngine()
        sched = MarketLifecycleScheduler(engine=eng, repository_getter=None)
        sched._get_repo = AsyncMock(return_value=StubRepo([make_pos(direction, entry, current, qty)]))
        sched._is_trading_day = MagicMock(return_value=True)
        await sched.on_auto_squareoff()
        return eng.captured[0]

    pnl, pct = await run("BUY", 100.0, 105.0, 10)
    report("P1", "scheduler BUY 100->105 x10 squares off at +50", pnl == 50.0 and abs(pct - 5.0) < 1e-9,
           f"pnl={pnl:+.0f} pct={pct:+.1f}%")

    pnl, pct = await run("SELL", 100.0, 95.0, 10)
    report("P2", "scheduler SELL 100->95 x10 squares off at +50", pnl == 50.0 and abs(pct - 5.0) < 1e-9,
           f"pnl={pnl:+.0f} pct={pct:+.1f}%")

    pnl, _ = await run("BUY", 100.0, 95.0, 10)
    report("P3", "scheduler BUY 100->95 x10 stays a loss (-50)", pnl == -50.0, f"pnl={pnl:+.0f}")


# ---------------------------------------------------------------------------
async def probe_close_position_recompute() -> None:
    from core.engine import UltraBotEngine

    def make_engine():
        eng = MagicMock(spec=UltraBotEngine)
        eng.broker = None
        eng.session_id = "probe"
        eng._errors_count = 0
        eng.config = MagicMock()
        eng.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
        eng.daily_risk = None
        eng.error_engine = MagicMock()
        eng.error_engine.handle_error = AsyncMock()
        eng._broadcast = AsyncMock()
        eng._route_alert = AsyncMock()
        repo = MagicMock()
        repo.update_trade = AsyncMock()
        repo.update_position = AsyncMock()

        class Ctx:
            async def __aenter__(self):
                return repo

            async def __aexit__(self, *a):
                pass

        eng._repo_context = MagicMock(return_value=Ctx())
        eng._close_position = UltraBotEngine._close_position.__get__(eng, UltraBotEngine)
        return eng, repo

    pos = SimpleNamespace(
        id="p1", trade_id="t1", symbol="TCS", strategy="ORB", direction="BUY",
        quantity=10, entry_price=100.0, current_price=105.0, stop_loss=95.0,
        target=110.0, entry_time="2026-09-01T10:00:00+05:30", extra=None,
    )

    eng, repo = make_engine()
    await eng._close_position(
        position=pos, exit_price=105.0, close_reason="auto_squareoff",
        pnl_amount=-50.0, pnl_pct=-5.0,  # the OLD inverted caller values
    )
    _, kwargs = repo.update_trade.await_args
    report("P4", "_close_position overrides caller's inverted -50 with +50",
           kwargs["pnl"] == 50.0, f"recorded pnl={kwargs['pnl']:+.1f}")

    pos2 = SimpleNamespace(**{**pos.__dict__, "direction": "SELL", "current_price": 95.0})
    eng2, repo2 = make_engine()
    await eng2._close_position(position=pos2, exit_price=95.0, close_reason="MANUAL")
    _, kwargs2 = repo2.update_trade.await_args
    report("P5", "manual-close style call recomputes pnl (+50), no TypeError",
           kwargs2["pnl"] == 50.0, f"recorded pnl={kwargs2['pnl']:+.1f}")


# ---------------------------------------------------------------------------
async def probe_dashboard_fallback() -> None:
    from api.routes.dashboard import get_dashboard

    pos = SimpleNamespace(
        id="p1", trade_id="t1", symbol="TCS", direction="BUY", strategy="ORB",
        entry_price=100.0, current_price=105.0, quantity=10,
        stop_loss=95.0, target=110.0, extra=None, entry_time=None,
    )
    repo = MagicMock()
    repo.get_open_positions = AsyncMock(return_value=[pos])
    repo.get_todays_pnl = AsyncMock(return_value={"realized": 0.0, "unrealized": 0.0})
    repo.get_todays_trades = AsyncMock(return_value=[])
    repo.get_watchlist_count = AsyncMock(return_value=0)
    data = await get_dashboard(username="probe", engine=None, repo=repo)
    row = data["open_positions"][0]
    report("P6", "dashboard fallback BUY 100->105 x10 unrealized = +50",
           row["unrealized_pnl"] == 50.0, f"unrealized={row['unrealized_pnl']:+.1f}")


# ---------------------------------------------------------------------------
async def probe_g16_regression() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    from test_g16_trend_wiring import _make_engine
    from risk.risk_engine import RiskEngine

    engine = _make_engine()
    engine.current_regime = "Bear"
    sig = {"symbol": "RELIANCE", "strategy": "ORB", "direction": "BUY",
           "confidence": 0.9, "entry_price": 2500.0, "quantity": 1}
    ctx = await engine._build_risk_context(sig, "RELIANCE", 2500.0, open_positions=[])
    ctx["current_time"] = datetime.combine(datetime.now(IST).date(), time(11, 0), tzinfo=IST)
    r = await RiskEngine({"max_open_positions": 3, "max_daily_trades": 10}).evaluate(
        signal=sig, symbol="RELIANCE", context=ctx)
    report("P7", "G16 regression: BUY in Bear still blocked end-to-end",
           r.passed is False and r.blocked_by == "G16_MultiTimeframe",
           f"blocked_by={r.blocked_by}")


async def main() -> None:
    print("=" * 78)
    print("v0.4.4 evidence probe — direction fixes on real production wiring")
    print("=" * 78)
    await probe_scheduler_squareoff()
    await probe_close_position_recompute()
    await probe_dashboard_fallback()
    await probe_g16_regression()
    print("-" * 78)
    print(f"RESULT: {sum(results)}/{len(results)} probes passed")
    raise SystemExit(0 if all(results) else 1)


if __name__ == "__main__":
    asyncio.run(main())
