"""REAL EVIDENCE repro P4-2 — Late-start catch-up when the backend boots mid-market.

Scenario (the Phase 4 Gap 1 bug): the backend boots at, say, 13:45 IST on a
trading day. APScheduler's 08:45 cron trigger never backfires for missed runs,
so without the catch-up today's Top-10 watchlist would never be generated and
the engine would scan a stale/empty watchlist all day.

Pipeline under test (all REAL code + REAL Yahoo market data + REAL SQLite DB):
  run_startup_catchup() -> trading-day/time/fresh-day guards (real repo queries)
  -> on_pre_market_init(force=True) -> WatchlistBuilder.build_daily_watchlist()
  over the full 50-symbol F&O universe with the REAL Yahoo feed -> persisted
  to the real watchlist table.

Decision matrix verified:
  Case 1: fresh day + market hours now      -> catch-up RUNS   (watchlist built)
  Case 2: a session already exists today    -> catch-up SKIPS  (day in progress)
  Case 3: closed trades exist, no session   -> catch-up SKIPS  (day in progress)

Run during NSE market hours for the full live-data path (falls back to the
feed's degradation path outside market hours, still real code).
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def build_stack(db_path: Path):
    """Build the REAL production stack over a given SQLite DB path."""
    os.environ["DB_PATH"] = str(db_path)

    # Re-import database module state fresh for this DB path
    import importlib
    import db.database as dbmod
    importlib.reload(dbmod)

    from config.settings import Settings
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
    from core.scheduler import MarketLifecycleScheduler
    from strategies.registry import StrategyRegistry
    from strategies.adaptive_manager import AdaptiveManager
    from strategies.regime_detector import RegimeDetector
    from strategies.performance_tracker import PerformanceTracker

    settings = Settings()
    risk_cfg = settings.get_risk_config()
    capital_cfg = settings.get_capital_config()

    async def repo_getter():
        return Repository(dbmod.async_session_factory())

    feed_manager = FeedManager(primary=YahooHistoricalFeed(), backup=None)
    reg = StrategyRegistry(); reg.discover()

    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=RiskEngine(risk_cfg),
        position_sizer=PositionSizer(settings.get_position_sizing_config(), capital_cfg),
        partial_booker=PartialBooker(settings.get_partial_booking_config()),
        daily_risk_manager=DailyRiskManager(risk_cfg, total_capital=float(capital_cfg.get("virtual_capital", 100000))),
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

    scheduler = MarketLifecycleScheduler(engine=engine, repository_getter=repo_getter)
    return scheduler, engine, repo_getter, dbmod


async def count_active_watchlist(repo_getter) -> int:
    from db.repository import Repository  # noqa: F401
    repo = await repo_getter()
    try:
        items = await repo.get_active_watchlist()
        return len(items)
    finally:
        await repo.close()


async def main():
    print("=" * 78)
    print("REPRO P4-2 — Late-start catch-up on mid-market backend boot (real code)")
    print("=" * 78)
    now = datetime.now(IST)
    print(f"[time] {now.strftime('%Y-%m-%d %H:%M:%S IST')} (weekday={now.weekday()})")

    tmpdir = Path(tempfile.mkdtemp(prefix="ub_p4_2_"))
    ok = True

    # ── Case 1: fresh day, market hours -> catch-up must RUN ────────────
    print("\n── Case 1: fresh trading day, boot during market hours ──")
    scheduler, engine, repo_getter, dbmod = build_stack(tmpdir / "case1.db")
    await dbmod.init_db()

    before = await count_active_watchlist(repo_getter)
    t_start = asyncio.get_event_loop().time()
    ran = await scheduler.run_startup_catchup()
    elapsed = asyncio.get_event_loop().time() - t_start
    after = await count_active_watchlist(repo_getter)

    repo = await repo_getter()
    try:
        items = await repo.get_active_watchlist()
        syms = [i.symbol for i in items]
        updated_today = sum(1 for i in items if str(i.updated_at or "").startswith(now.date().isoformat()))
    finally:
        await repo.close()

    print(f"[case1] catch-up ran: {ran}   watchlist: {before} -> {after} active items   ({elapsed:.1f}s)")
    print(f"[case1] active symbols: {syms}")
    print(f"[case1] items updated today: {updated_today}/{after}")
    case1_ok = ran is True and after >= 1 and updated_today == after
    print(f"[case1] {'PASS' if case1_ok else 'FAIL'}")
    ok &= case1_ok

    # ── Case 2: a session already exists today -> catch-up must SKIP ────
    print("\n── Case 2: mid-day restart, session already exists today ──")
    sm = engine.session_manager
    await sm.create_session(mode="paper", broker="paper", initial_capital=500000.0)
    ran2 = await scheduler.run_startup_catchup()
    after2 = await count_active_watchlist(repo_getter)
    print(f"[case2] catch-up ran: {ran2}   watchlist still: {after2} active items (unchanged)")
    case2_ok = ran2 is False and after2 == after
    print(f"[case2] {'PASS' if case2_ok else 'FAIL'}")
    ok &= case2_ok

    # ── Case 3: closed trades today, no session -> catch-up must SKIP ───
    print("\n── Case 3: closed trades exist today, no session ──")
    scheduler3, engine3, repo_getter3, dbmod3 = build_stack(tmpdir / "case3.db")
    await dbmod3.init_db()
    repo = await repo_getter3()
    try:
        await repo.create_trade(
            id="trade-p4-c3",
            symbol="RELIANCE",
            direction="LONG",
            strategy="ORB",
            entry_price=1000.0,
            quantity=10,
            status="CLOSED",
            entry_time=datetime.now(IST).isoformat(),
            exit_time=datetime.now(IST).isoformat(),
            net_pnl=-250.0,
        )
    finally:
        await repo.close()
    ran3 = await scheduler3.run_startup_catchup()
    print(f"[case3] catch-up ran: {ran3}")
    case3_ok = ran3 is False
    print(f"[case3] {'PASS' if case3_ok else 'FAIL'}")
    ok &= case3_ok

    print("\n" + "=" * 78)
    print(f"REPRO P4-2 RESULT: {'ALL CHECKS PASSED — startup catch-up verified' if ok else 'FAILURES DETECTED'}")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
