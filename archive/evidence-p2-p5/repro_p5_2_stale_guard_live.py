#!/usr/bin/env python3
"""Phase 5 Evidence — runtime stale-data guard over REAL feed data.

Uses the REAL YahooHistoricalFeed (production code path) and a REAL engine:
  Case 1: RELIANCE live candles (real fetch) — newest bar must be fresh, the
          G16 gate must NOT block it (no false positive on live data).
  Case 2: same REAL candles with timestamps aged 400 days — the G16 gate must
          fire (SKIPPED + DATA_STALE_CANDLES telemetry). Only the candle clock is
          simulated; prices/volumes are real fetched data.
  Case 3: TATAMOTORS (really delisted Oct-2025) via the real feed — zero
          candles -> NO_SETUP; a dead symbol can never produce a signal.
"""
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

BACKEND = Path(__file__).resolve().parent.parent / "ultrabot-web" / "backend"
sys.path.insert(0, str(BACKEND))

from config.settings import Settings  # noqa: E402
from core.engine import UltraBotEngine, EngineState  # noqa: E402
from feeds.yahoo_historical import YahooHistoricalFeed  # noqa: E402
from utils.market_utils import get_last_candle_age_minutes  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"


def make_engine():
    cfg = MagicMock(spec=Settings)
    cfg.get_capital_config.return_value = {"virtual_capital": 500000.0}
    cfg.get_risk_config.return_value = {"stale_candle_max_age_minutes": 30}
    cfg.get_partial_booking_config.return_value = {}
    cfg.get_strategy_activation.return_value = {"active": [], "paused": []}
    engine = UltraBotEngine(
        config=cfg, repository_getter=None, error_engine=None, risk_engine=None,
        position_sizer=None, partial_booker=None, daily_risk_manager=None,
        broker_factory=None, feed_manager=None, session_manager=None,
        market_hours=None, ws_manager=None,
    )
    engine.state = EngineState.RUNNING
    engine.active_strategies = []
    engine._broadcast = MagicMock()
    return engine


def g16_events(engine):
    return [e for e in engine._recent_scan_telemetry if e.get("gate") == "DATA_STALE_CANDLES"]


async def main():
    feed = YahooHistoricalFeed()
    engine = make_engine()
    engine.market_hours = MagicMock()
    engine.market_hours.is_market_open.return_value = True  # simulated open session

    print(f"[{datetime.now():%H:%M:%S}] Case 1: RELIANCE real live candles must NOT be blocked")
    candles = await feed.get_candles("RELIANCE", timeframe="5m", count=100, force_refresh=True)
    age = get_last_candle_age_minutes(candles)
    print(f"  fetched {len(candles)} real 5m candles; newest bar age = {age:.1f} min")
    mock_feed = MagicMock()
    mock_feed.get_candles = AsyncMock(return_value=candles)
    engine.feed = mock_feed
    await engine._scan_symbol("RELIANCE", None)
    blocked = g16_events(engine)
    ok1 = len(candles) >= 20 and not blocked
    print(f"  -> {PASS if ok1 else FAIL}: {len(candles)} candles, G16 events: {len(blocked)}")

    print("Case 2: same REAL candles aged 400 days -> G16 must fire")
    aged = []
    for c in candles:
        c2 = dict(c)
        ts = datetime.fromisoformat(c["timestamp"]) - timedelta(days=400)
        c2["timestamp"] = ts.isoformat()
        aged.append(c2)
    mock_feed.get_candles = AsyncMock(return_value=aged)
    await engine._scan_symbol("RELIANCE", None)
    events = g16_events(engine)
    ok2 = len(events) == 1 and events[0]["status"] == "SKIPPED" and "RELIANCE" in engine._stale_data_symbols_warned
    print(f"  -> {PASS if ok2 else FAIL}: DATA_STALE_CANDLES event = {events[0]['reason'] if events else 'NONE'}")

    print("Case 3: TATAMOTORS (delisted) via real feed -> no signal possible")
    engine2 = make_engine()
    engine2.market_hours = MagicMock()
    engine2.market_hours.is_market_open.return_value = True
    engine2.feed = feed  # the REAL feed, not a mock
    await engine2._scan_symbol("TATAMOTORS", None)
    ev = engine2._recent_scan_telemetry
    ok3 = ev and ev[-1]["status"] == "NO_SETUP"
    print(f"  -> {PASS if ok3 else FAIL}: telemetry = {ev[-1]['reason'] if ev else 'NONE'}")

    print("Case 4: TMPV (successor) via real feed -> live data, no G16 block")
    tmpv_candles = await feed.get_candles("TMPV", timeframe="5m", count=100, force_refresh=True)
    mock_feed2 = MagicMock()
    mock_feed2.get_candles = AsyncMock(return_value=tmpv_candles)
    engine2.feed = mock_feed2
    engine2._recent_scan_telemetry.clear()
    await engine2._scan_symbol("TMPV", None)
    ok4 = len(tmpv_candles) >= 20 and not g16_events(engine2)
    print(f"  -> {PASS if ok4 else FAIL}: {len(tmpv_candles)} real candles scanned, G16 events: {len(g16_events(engine2))}")

    all_ok = ok1 and ok2 and ok3 and ok4
    print(f"\n{'ALL CASES PASS' if all_ok else 'SOME CASES FAILED'}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
