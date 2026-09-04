"""REAL EVIDENCE repro — C1 (daily-risk dead branch) + C8 (watchlist attr).

Runs the PRODUCTION wiring from app.py (real Settings, real SQLite DB,
real components, real UltraBotEngine). No mocks.

Usage: cd ultrabot-web/backend && ../../evidence/repro_1_*.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

# Isolated real DB for this repro
tmpdb = Path(tempfile.mkdtemp(prefix="ub_evidence_")) / "ultrabot.db"
os.environ["DB_PATH"] = str(tmpdb)

from config.settings import Settings
from db.database import init_db, async_session_factory
from db.repository import Repository
from risk.daily_risk_manager import DailyRiskManager

PASS, FAIL = "\033[92mCONFIRMED\033[0m", "\033[91mNOT-REPRODUCED\033[0m"


async def main():
    print("=" * 72)
    print("REPRO 1 — C1 daily-risk dead branch + C8 missing watchlist attribute")
    print("=" * 72)

    await init_db()
    settings = Settings()

    # ---------- C1: engine guard references a method that does not exist ----------
    risk_cfg = settings.get_risk_config()
    capital_cfg = settings.get_capital_config()
    daily_risk = DailyRiskManager(risk_cfg, total_capital=float(capital_cfg.get("virtual_capital", 100000)))

    has_wrong = hasattr(daily_risk, "record_trade")
    has_right = hasattr(daily_risk, "record_trade_result")
    print(f"\n[C1] DailyRiskManager has 'record_trade'        : {has_wrong}")
    print(f"[C1] DailyRiskManager has 'record_trade_result' : {has_right}")
    print(f"[C1] engine.py:2604 guard `hasattr(..., 'record_trade')` -> {has_wrong}")
    verdict = (not has_wrong) and has_right
    print(f"[C1] => daily-loss/trade-count update block is DEAD CODE: {PASS if verdict else FAIL}")

    # Prove functional impact: simulate exactly what the engine block would do
    # if it were wired to the REAL method, vs what happens today.
    before_pnl, before_trades = daily_risk.daily_pnl, daily_risk.daily_trades
    # what the engine *attempts* today (guarded out):
    if hasattr(daily_risk, "record_trade"):
        daily_risk.record_trade(pnl=-5000.0)
    after_attempt_pnl = daily_risk.daily_pnl
    print(f"[C1] after engine-attempted update: daily_pnl {before_pnl} -> {after_attempt_pnl} (unchanged = gates blind)")
    # what the REAL method does:
    daily_risk.record_trade_result(pnl=-5000.0)
    print(f"[C1] after record_trade_result(-5000): daily_pnl {before_pnl} -> {daily_risk.daily_pnl}, trades {before_trades} -> {daily_risk.daily_trades}")
    status = daily_risk.check_daily_limits()
    print(f"[C1] check_daily_limits() after -5000 real loss: can_trade={getattr(status, 'can_trade', status)}")

    # ---------- C8: engine.watchlist attribute ----------
    from core.engine import UltraBotEngine
    engine = UltraBotEngine.__new__(UltraBotEngine)  # no __init__ side effects
    print(f"\n[C8] engine class docs: reading 'self.watchlist' usage sites:")
    import subprocess
    out = subprocess.run(
        ["grep", "-n", "self.watchlist", str(BACKEND / "core" / "engine.py")],
        capture_output=True, text=True,
    ).stdout.strip()
    print("     " + out.replace("\n", "\n     "))
    src = (BACKEND / "core" / "engine.py").read_text()
    init_assigns = [l for l in src.splitlines() if "self.watchlist" in l and "=" in l and "==" not in l and "get_active_watchlist" not in l]
    print(f"[C8] assignment sites for self.watchlist (self.watchlist = ...): {init_assigns or 'NONE'}")

    # Runtime proof with full production construction (same as app.py):
    from errors.error_engine import ErrorEngine
    from risk.risk_engine import RiskEngine
    from risk.position_sizer import PositionSizer
    from risk.partial_booker import PartialBooker
    from feeds.yahoo_historical import YahooHistoricalFeed
    from feeds.feed_manager import FeedManager
    from core.market_hours import MarketHours
    from core.session_manager import SessionManager
    from strategies.registry import StrategyRegistry
    from strategies.adaptive_manager import AdaptiveManager
    from strategies.regime_detector import RegimeDetector
    from strategies.performance_tracker import PerformanceTracker

    async def repo_getter():
        return Repository(async_session_factory())

    reg = StrategyRegistry(); reg.discover()
    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=RiskEngine(risk_cfg),
        position_sizer=PositionSizer(settings.get_position_sizing_config(), capital_cfg),
        partial_booker=PartialBooker(settings.get_partial_booking_config()),
        daily_risk_manager=daily_risk,
        broker_factory=None,
        feed_manager=FeedManager(primary=YahooHistoricalFeed(), backup=None),
        session_manager=SessionManager(repo_getter),
        market_hours=MarketHours(),
        ws_manager=None,
        strategy_registry=reg,
        adaptive_manager=AdaptiveManager(config={"activation_map": settings._raw_config.get("strategy_activation", {})}, registry=reg, regime_detector=RegimeDetector()),
        regime_detector=RegimeDetector(),
        performance_tracker=PerformanceTracker(),
    )
    has_attr = hasattr(engine, "watchlist")
    print(f"[C8] production-constructed engine has 'watchlist' attr: {has_attr}")

    # Now actually run _scan_watchlist() against the empty REAL DB:
    engine.state = type("S", (), {"value": "running"})()  # minimal state shim
    try:
        await engine._scan_watchlist()
        print(f"[C8] _scan_watchlist() on empty DB watchlist: completed WITHOUT error => {FAIL}")
    except AttributeError as e:
        print(f"[C8] _scan_watchlist() on empty DB watchlist raised AttributeError: {e} => {PASS}")
    except Exception as e:
        print(f"[C8] _scan_watchlist() raised {type(e).__name__}: {e}")

    print("\nDB used:", tmpdb)


if __name__ == "__main__":
    asyncio.run(main())
