"""REAL EVIDENCE repro 3 — Full engine scan cycle with REAL Yahoo data.

Production wiring (same as app.py): real Settings, real SQLite DB seeded with a
real watchlist, real Yahoo 5m candles, real v2 strategies, real risk gates,
real opportunity construction. NO MOCKS anywhere in the engine path.

What this proves/disproves:
 - candles fetch -> DataFrame conversion -> strategy scan (no crashes)
 - signals (if any on real data) flow through 16 gates -> sizing -> opportunity
 - telemetry shows per-symbol/per-strategy outcomes
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

tmpdb_dir = Path(tempfile.mkdtemp(prefix="ub_e3_"))
os.environ["DB_PATH"] = str(tmpdb_dir / "ultrabot.db")

import logging
logging.basicConfig(level=logging.WARNING)

from config.settings import Settings
from db.database import init_db, async_session_factory
from db.repository import Repository
from errors.error_engine import ErrorEngine
from risk.risk_engine import RiskEngine
from risk.daily_risk_manager import DailyRiskManager
from risk.position_sizer import PositionSizer
from risk.partial_booker import PartialBooker
from feeds.yahoo_historical import YahooHistoricalFeed
from feeds.feed_manager import FeedManager
from core.engine import UltraBotEngine, EngineState
from core.market_hours import MarketHours
from core.session_manager import SessionManager
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker

SYMBOLS = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "SBIN"]


async def main():
    print("=" * 72)
    print("REPRO 3 — REAL engine scan cycle (real DB, real Yahoo data, real gates)")
    print("=" * 72)

    await init_db()
    settings = Settings()
    risk_cfg = settings.get_risk_config()
    capital_cfg = settings.get_capital_config()

    async def repo_getter():
        return Repository(async_session_factory())

    # Seed a real watchlist
    repo = await repo_getter()
    try:
        for s in SYMBOLS:
            await repo.add_watchlist_item(symbol=s, name=s, added_by="evidence")
        wl = await repo.get_active_watchlist()
        print(f"[DB ] watchlist seeded: {[w.symbol for w in wl]}")
    finally:
        await repo.close()

    reg = StrategyRegistry(); reg.discover()
    print(f"[REG] strategies discovered: {sorted(reg.list_names())[:20] if hasattr(reg, 'list_names') else 'n/a'}")

    feed_manager = FeedManager(primary=YahooHistoricalFeed(), backup=None)
    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=RiskEngine(risk_cfg),
        position_sizer=PositionSizer(settings.get_position_sizing_config(), capital_cfg),
        partial_booker=PartialBooker(settings.get_partial_booking_config()),
        daily_risk_manager=DailyRiskManager(risk_cfg, total_capital=float(capital_cfg.get("virtual_capital", 100000))),
        broker_factory=None,
        feed_manager=feed_manager,
        session_manager=SessionManager(repo_getter),
        market_hours=MarketHours(),
        ws_manager=None,
        strategy_registry=reg,
        adaptive_manager=AdaptiveManager(config={"activation_map": settings._raw_config.get("strategy_activation", {})}, registry=reg, regime_detector=RegimeDetector()),
        regime_detector=RegimeDetector(),
        performance_tracker=PerformanceTracker(),
    )
    engine.state = EngineState.RUNNING
    engine.active_strategies = ["orb", "mb", "mrf", "ptc", "sic", "trs", "vc"]

    # ---- market context (real NIFTY + VIX from Yahoo) ----
    print("\n[CTX] _update_market_context() with real Yahoo data ...")
    await engine._update_market_context()
    print(f"[CTX] nifty_price={engine.nifty_price} vix={engine.vix} regime={engine.current_regime}")

    # ---- one full scan cycle ----
    print("\n[SCAN] _scan_watchlist() over", SYMBOLS, "...")
    try:
        await engine._scan_watchlist()
        print("[SCAN] completed without exception")
    except AttributeError as e:
        print(f"[SCAN] AttributeError (C8 watchlist fallback path): {e}")
    except Exception as e:
        print(f"[SCAN] {type(e).__name__}: {e}")

    # ---- validate pending opportunities (TTL engine) ----
    try:
        await engine._validate_pending_opportunities()
        print("[TTL ] _validate_pending_opportunities() ok")
    except Exception as e:
        print(f"[TTL ] {type(e).__name__}: {e}")

    # ---- telemetry (what actually happened per symbol/strategy) ----
    t = engine.get_scan_telemetry()
    print(f"\n[TELE] signals_generated={t.get('signals_generated')} opportunities_created={t.get('opportunities_created', 'n/a')}")
    print(f"[TELE] active_strategies={t.get('active_strategies')}")
    for ev in t.get("recent_events", [])[:40]:
        print(f"       {ev.get('symbol','?'):10s} {ev.get('strategy','?'):5s} {ev.get('status','?'):12s} {ev.get('reason','')}")
    print(f"\n[OPPS] pending={list(engine.pending_opportunities.keys())}")
    print(f"[OPPS] invalidated={list(engine.invalidated_opportunities.keys())}")

    print("\nDB:", tmpdb_dir / 'ultrabot.db')


if __name__ == "__main__":
    asyncio.run(main())
