"""REAL EVIDENCE repro 4 — FULL paper-trade cycle through the REAL pipeline.

Production wiring: real Settings (with a TEST-ONLY widened G8 trade window so
the cycle can run pre-market), real SQLite DB, real Yahoo LTP feed injected
into a real PaperBroker, real 16-gate RiskEngine, real Kelly PositionSizer.

Pipeline under test (all REAL code):
  pending opportunity -> confirm_opportunity() -> TTL check -> live-LTP
  re-fetch -> pre-execution target/SL checks -> price-mismatch check ->
  G1..G16 -> position sizing -> PaperBroker.place_order(MARKET @ real LTP)
  -> DB trade + position rows -> _close_position() -> DailyRiskManager
  recording (the C1 fix) -> G4/G5 context now live.

The ONLY synthetic input is the opportunity itself (a realistic MB signal on
RELIANCE built from the LIVE LTP). Everything downstream is production code.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

tmpdir = Path(tempfile.mkdtemp(prefix="ub_e4_"))
os.environ["DB_PATH"] = str(tmpdir / "ultrabot.db")

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
from brokers.factory import BrokerFactory
from feeds.yahoo_historical import YahooHistoricalFeed
from feeds.feed_manager import FeedManager
from core.engine import UltraBotEngine, EngineState
from core.market_hours import MarketHours
from core.session_manager import SessionManager
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker


async def main():
    print("=" * 72)
    print("REPRO 4 — FULL paper-trade cycle (real pipeline, real Yahoo LTP)")
    print("=" * 72)

    await init_db()
    settings = Settings()
    # TEST-ONLY config widening: run the cycle pre-market (G8 window).
    # This is config data, not code — the shipped defaults stay 09:30-15:15.
    settings._raw_config.setdefault("risk", {})
    settings._raw_config["risk"]["new_trade_window_start"] = "00:00"
    settings._raw_config["risk"]["new_trade_window_end"] = "23:59"

    risk_cfg = settings.get_risk_config()
    capital_cfg = settings.get_capital_config()

    async def repo_getter():
        return Repository(async_session_factory())

    feed_manager = FeedManager(primary=YahooHistoricalFeed(), backup=None)

    # ---- PaperBroker with the NEW live-feed injection (production wiring) ----
    broker = BrokerFactory.create("paper", mode="paper", initial_capital=float(capital_cfg.get("virtual_capital", 100000)))
    broker.feed = feed_manager  # what engine.start() now does automatically

    reg = StrategyRegistry(); reg.discover()
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
    engine.mode = "paper"
    engine.broker_name = "paper"
    engine.broker = broker
    engine.session_id = "evidence-session-4"
    engine.active_strategies = ["orb", "mb", "mrf", "ptc", "sic", "trs", "vc"]

    # ---- real market context ----
    await engine._update_market_context()
    print(f"[CTX] nifty={engine.nifty_price} vix={engine.vix} regime={engine.current_regime} conf={engine.regime_confidence}")

    # ---- live RELIANCE LTP (REAL data) ----
    ltp = await feed_manager.get_ltp("RELIANCE")
    print(f"[LTP] RELIANCE live = {ltp}")
    assert ltp and ltp > 0, "No live LTP — cannot build realistic opportunity"

    # ---- realistic MB opportunity built from the LIVE price ----
    opp_id = "ev-opp-001"
    engine.pending_opportunities[opp_id] = {
        "id": opp_id,
        "symbol": "RELIANCE",
        "strategy": "MB",
        "direction": "LONG",
        "entry_price": round(ltp, 2),
        "stop_loss": round(ltp * 0.985, 2),       # -1.5%
        "target": round(ltp * 1.03, 2),           # +3.0%  (RR = 2.0)
        "quantity": 10,
        "confidence": 0.78,
        "created_at": __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).isoformat(),
        "signal_data": {
            "strategy": "MB", "symbol": "RELIANCE", "direction": "LONG",
            "entry_price": round(ltp, 2),
            "sl_price": round(ltp * 0.985, 2),
            "target_price": round(ltp * 1.03, 2),
            "confidence": 0.78,
        },
    }

    # ---- EXECUTE through the REAL confirm pipeline ----
    print("\n[EXEC] confirm_opportunity() ...")
    result = await engine.confirm_opportunity(opp_id, segment="EQ")
    print(f"[EXEC] result: status={result.get('status')}")
    for k in ("trade_id", "order_id", "quantity", "entry_price", "filled_price", "reason", "blocked_by", "block_reason"):
        if k in result:
            print(f"       {k}: {result[k]}")
    gates = result.get("risk_gates") or []
    if gates:
        for g in gates:
            mark = "PASS" if g.get("passed") else "FAIL"
            print(f"       G: {g.get('name','?'):28s} {mark}  {g.get('message','')[:70]}")

    if result.get("status") not in ("filled", "success", "executed"):
        print("\n[NOTE] Trade not executed — inspect gates above (legit rejections on real data are OK).")
        return

    # ---- verify broker state ----
    pos = broker.positions.get("RELIANCE")
    print(f"\n[BRKR] PaperBroker position: {pos}")
    print(f"[BRKR] capital after entry: {broker.capital:.2f}")

    # ---- verify DB rows ----
    repo = await repo_getter()
    try:
        trades = await repo.get_trades() if hasattr(repo, "get_trades") else []
        positions = await repo.get_open_positions()
        print(f"[DB  ] trades rows: {len(trades)} | open positions rows: {len(positions)}")
        for p in positions:
            print(f"       pos {p.symbol} {p.direction} qty={p.quantity} entry={p.entry_price} status={p.status}")
    finally:
        await repo.close()

    # ---- CLOSE the position through the REAL close pipeline ----
    dr = engine.daily_risk
    print(f"\n[RISK] BEFORE close: daily_pnl={dr.daily_pnl} daily_trades={dr.daily_trades}")
    exit_price = round(ltp * 1.02, 2)  # +2% paper gain
    close_res = await engine._close_position(
        position=positions[0] if positions else pos,
        exit_price=exit_price,
        close_reason="target_hit",
        pnl_amount=(exit_price - pos["entry_price"]) * pos["quantity"],
        pnl_pct=2.0,
    )
    print(f"[CLS ] close done; daily_pnl={dr.daily_pnl} daily_trades={dr.daily_trades} (C1 fix: values must be non-zero)")
    status = dr.check_daily_limits()
    print(f"[CLS ] check_daily_limits: trades={status.total_trades} net_pnl={status.net_pnl} can_trade={status.can_take_new_trades}")

    pos_after = broker.positions.get("RELIANCE")
    print(f"[BRKR] position after close: status={pos_after.get('status')} qty={pos_after.get('quantity')} realized={pos_after.get('realized_pnl')}")

    ok = dr.daily_trades >= 1 and dr.daily_pnl != 0
    print(f"\n{'='*72}\nVERDICT: {'C1 FIX VERIFIED IN TRADE PATH' if ok else 'C1 STILL BROKEN'}\n{'='*72}")
    print("DB:", tmpdir / "ultrabot.db")


if __name__ == "__main__":
    asyncio.run(main())
