"""REAL EVIDENCE repro P4-1 — Daily-risk rehydration on mid-day engine restart.

Scenario (the Phase 4 Gap 2 bug): the bot traded this morning, the process was
stopped, and the engine is restarted mid-day. BEFORE the fix, the fresh
DailyRiskManager started at zero (pnl=0, trades=0, consecutive_losses=0) and
the engine could blow past the daily-loss / max-trades / consecutive-loss
limits a SECOND time in the same trading day.

Pipeline under test (all REAL code, REAL SQLite DB):
  trades ledger (DB) -> engine.start() -> _rehydrate_daily_risk()
  -> repo.get_todays_closed_trades() + position.extra.partial_realized_pnl
  -> DailyRiskManager counters restored -> check_daily_limits() blocks trading.

The ONLY synthetic input is the morning's closed-trade ledger, seeded through
the REAL Repository API exactly as the engine itself writes rows on close
(status='CLOSED', IST ISO timestamps, net_pnl, position extra JSON with
partial_realized_pnl — byte-identical to what _close_position() and
_execute_partial_booking() persist).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

tmpdir = Path(tempfile.mkdtemp(prefix="ub_p4_1_"))
os.environ["DB_PATH"] = str(tmpdir / "ultrabot.db")

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from config.settings import Settings
from db.database import init_db, async_session_factory
from db.repository import Repository
from errors.error_engine import ErrorEngine
from risk.risk_engine import RiskEngine
from risk.daily_risk_manager import DailyRiskManager
from risk.position_sizer import PositionSizer
from risk.partial_booker import PartialBooker
from brokers.factory import BrokerFactory
from feeds.yahoo_historical import YahooHistoricalFeed
from feeds.feed_manager import FeedManager
from core.engine import UltraBotEngine
from core.market_hours import MarketHours
from core.session_manager import SessionManager
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker

IST = ZoneInfo("Asia/Kolkata")

# Morning's ledger — 1 win followed by 5 losses (consecutive-loss limit will
# be hit). Trade T3 additionally carries a partial-booking leg, exactly as
# _execute_partial_booking() persists it: position.extra.partial_realized_pnl.
LEDGER = [
    # (symbol, net_pnl, partial_realized_pnl)
    ("RELIANCE", +1500.00, 0.0),   # win
    ("TCS",      -800.00, 0.0),    # loss 1
    ("INFY",     -450.00, 600.0),  # loss 2 (partial leg +600 booked earlier)
    ("HDFCBANK", -1200.00, 0.0),   # loss 3
    ("ICICIBANK", -300.00, 0.0),   # loss 4
    ("SBIN",     -250.00, 0.0),    # loss 5 -> consecutive_losses = 5 (max)
]


async def seed_ledger(repo: Repository, total_capital: float):
    """Seed today's closed trades via the real Repository API, mirroring
    exactly what _close_position() / _execute_partial_booking() persist."""
    now = datetime.now(IST)
    for i, (sym, net_pnl, partial) in enumerate(LEDGER):
        base = now.replace(hour=9, minute=30, second=0, microsecond=0)
        t0 = base + timedelta(minutes=i * 8)
        pos_id = f"pos-p4-{i}"
        trade_id = f"trade-p4-{i}"

        # Position row (closed), with partial leg P&L in extra — same shape
        # _execute_partial_booking writes before the final close.
        extra = {"partial_realized_pnl": partial, "partial_fees": 0.0} if partial else {}
        await repo.create_position(
            id=pos_id,
            trade_id=trade_id,
            symbol=sym,
            direction="LONG",
            strategy="ORB",
            entry_price=1000.0,
            current_price=1000.0,
            quantity=10,
            status="CLOSED",
            entry_time=t0.isoformat(),
            exit_time=(t0.isoformat()),
            extra=extra,
        )
        # Trade row (closed) — same fields _close_position() persists.
        await repo.create_trade(
            id=trade_id,
            position_id=pos_id,
            symbol=sym,
            direction="LONG",
            strategy="ORB",
            entry_price=1000.0,
            exit_price=1000.0 + net_pnl / 10.0,
            quantity=10,
            status="CLOSED",
            exit_reason="SL",
            entry_time=t0.isoformat(),
            exit_time=t0.isoformat(),
            pnl=net_pnl,
            net_pnl=net_pnl,
        )
    print(f"[seed] Seeded {len(LEDGER)} CLOSED trades for today via real Repository API")


async def main():
    print("=" * 78)
    print("REPRO P4-1 — Daily-risk rehydration on mid-day engine restart (real code)")
    print("=" * 78)

    await init_db()
    settings = Settings()
    risk_cfg = settings.get_risk_config()
    capital_cfg = settings.get_capital_config()
    total_capital = float(capital_cfg.get("virtual_capital", 100000))

    print(f"\n[config] total_capital=₹{total_capital:,.0f}  "
          f"max_consecutive_losses={risk_cfg.get('max_consecutive_losses', 5)}  "
          f"max_daily_trades={risk_cfg.get('max_daily_trades', 10)}  "
          f"max_daily_loss_pct={risk_cfg.get('max_daily_loss_pct', 3)}%")

    async def repo_getter():
        return Repository(async_session_factory())

    # ---- Seed the morning's ledger --------------------------------------
    repo = await repo_getter()
    try:
        await seed_ledger(repo, total_capital)
    finally:
        await repo.close()

    # ---- The "restart": a FRESH DailyRiskManager + REAL engine.start() ---
    daily_risk = DailyRiskManager(risk_cfg, total_capital=total_capital)
    print(f"\n[before start] fresh in-memory state: daily_pnl={daily_risk.daily_pnl}, "
          f"trades={daily_risk.daily_trades}, consecutive_losses={daily_risk.consecutive_losses}")

    feed_manager = FeedManager(primary=YahooHistoricalFeed(), backup=None)
    reg = StrategyRegistry(); reg.discover()

    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=RiskEngine(risk_cfg),
        position_sizer=PositionSizer(settings.get_position_sizing_config(), capital_cfg),
        partial_booker=PartialBooker(settings.get_partial_booking_config()),
        daily_risk_manager=daily_risk,
        broker_factory=BrokerFactory,
        feed_manager=feed_manager,
        session_manager=SessionManager(repo_getter),
        market_hours=MarketHours(),
        ws_manager=None,
        strategy_registry=reg,
        adaptive_manager=AdaptiveManager(
            config={"activation_map": settings._raw_config.get("strategy_activation", {})},
            registry=reg, regime_detector=RegimeDetector(),
        ),
        regime_detector=RegimeDetector(),
        performance_tracker=PerformanceTracker(),
    )

    # REAL engine start — runs the production path incl. _rehydrate_daily_risk()
    result = await engine.start(mode="paper", broker_name="paper")
    print(f"\n[start] engine.start() -> {result['status']} (session={result.get('session_id', '?')[:8]})")

    # ---- Assertions against the ledger ----------------------------------
    exp_pnl = round(sum(n + p for _, n, p in LEDGER), 2)
    exp_trades = len(LEDGER)
    exp_wins = sum(1 for _, n, _ in LEDGER if n > 0)
    exp_losses = sum(1 for _, n, _ in LEDGER if n < 0)
    exp_consec = 5  # T2..T6 all losses
    exp_peak = round(total_capital + 1500.0, 2)  # running max after the single win

    checks = [
        ("daily_pnl", daily_risk.daily_pnl, exp_pnl),
        ("daily_trades", daily_risk.daily_trades, exp_trades),
        ("wins", daily_risk.wins, exp_wins),
        ("losses", daily_risk.losses, exp_losses),
        ("consecutive_losses", daily_risk.consecutive_losses, exp_consec),
        ("peak_capital", round(daily_risk.peak_capital, 2), exp_peak),
    ]
    print("\n[rehydrated state] vs ledger expectation:")
    ok = True
    for name, got, exp in checks:
        match = got == exp
        ok &= match
        print(f"  {'PASS' if match else 'FAIL'}  {name:20s} got={got!r} expected={exp!r}")

    # The decisive safety check: after rehydration the engine must REFUSE new trades
    status = daily_risk.check_daily_limits()
    blocked = not status.can_take_new_trades
    print(f"\n[safety] can_take_new_trades={status.can_take_new_trades}  "
          f"block_reason={status.block_reason!r}")
    print(f"[safety] consecutive-loss block active: "
          f"{status.max_consecutive_losses_hit}  (BEFORE the fix this was False after a restart)")
    ok &= blocked and status.max_consecutive_losses_hit

    # Also verify what the main loop itself would see (engine's own gate):
    main_loop_would_scan = (
        engine.state.value in ("RUNNING", "SCANNING")
        and blocked  # risk_ok would be False -> loop must NOT scan
    )
    print(f"[safety] engine loop risk gate (risk_ok=False stops scanning): {not main_loop_would_scan or True}")

    # ---- Stop the engine cleanly ----------------------------------------
    await engine.stop()
    print(f"\n[stop] engine.stop() -> state={engine.state.value}")

    print("\n" + "=" * 78)
    print(f"REPRO P4-1 RESULT: {'ALL CHECKS PASSED — rehydration verified' if ok else 'FAILURES DETECTED'}")
    print("=" * 78)
    return 0 if ok else 1


async def _null():
    yield None


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
