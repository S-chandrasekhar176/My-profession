import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.settings import settings
from core.engine import UltraBotEngine
from core.market_hours import MarketHours, IST
from core.capital_resolver import resolve_total_capital
from core.session_manager import SessionManager
from errors.error_engine import ErrorEngine
from db.database import async_session_factory, init_db
from db.repository import Repository
from feeds.base import BaseFeed
from feeds.feed_manager import FeedManager
from risk.daily_risk_manager import DailyRiskManager
from risk.position_sizer import PositionSizer
from risk.partial_booker import PartialBooker
from risk.risk_engine import RiskEngine
from brokers.factory import BrokerFactory
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker
from scanner.kronos.kronos_scanner import KronosScanner
from notifications.alert_manager import AlertManager
from notifications.telegram_bot import TelegramBot
from api.websocket import ws_manager


class MockWatchdogFeed(BaseFeed):
    def __init__(self, name: str = "mock_primary"):
        self.name = name
        self.ltp_value: float = 2500.0
        self.candles_to_return: List[Dict[str, Any]] = [
            {
                "timestamp": datetime.now(IST).isoformat(),
                "open": 2500.0,
                "high": 2510.0,
                "low": 2490.0,
                "close": 2505.0,
                "volume": 1000,
            }
        ]
        self.connected_state = True
        self.probe_calls = 0

    def get_name(self) -> str:
        return self.name

    def is_connected(self) -> bool:
        return self.connected_state

    async def connect(self) -> Dict[str, Any]:
        return {"success": True}

    async def disconnect(self) -> Dict[str, Any]:
        return {"success": True}

    async def subscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True}

    async def unsubscribe(self, symbols: List[str]) -> Dict[str, Any]:
        return {"success": True}

    async def get_ltp(self, symbol: str) -> float:
        self.probe_calls += 1
        if self.ltp_value and self.ltp_value > 0:
            return float(self.ltp_value)
        return 0.0

    async def get_candles(self, symbol: str, timeframe: str = "5m", count: int = 100, **kwargs) -> List[Dict[str, Any]]:
        return list(self.candles_to_return)

    async def get_latest_price(self, symbol: str) -> float:
        return await self.get_ltp(symbol)


@pytest.fixture
def feed_manager():
    mock_feed = MockWatchdogFeed()
    return FeedManager(primary=mock_feed, backup=None, watchdog_interval_seconds=120.0), mock_feed


@pytest.mark.asyncio
async def test_passive_traffic_confirmation_on_non_empty_candles_and_ltp(feed_manager):
    """When non-empty data is received, health_check confirms HEALTHY via passive traffic without probing."""
    fm, mock_feed = feed_manager

    # 1. Fetch non-empty candles
    candles = await fm.get_candles("RELIANCE")
    assert len(candles) == 1
    assert fm._primary_failure_count == 0
    assert fm._primary_healthy is True
    assert fm._last_successful_fetch_time > 0

    # 2. Health check runs within watchdog interval -> passive confirmation, 0 probe calls
    mock_feed.probe_calls = 0
    health = await fm.health_check()
    assert health["healthy"] is True
    assert health["status"] == "HEALTHY"
    assert health["check_mode"] == "passive_traffic"
    assert mock_feed.probe_calls == 0


@pytest.mark.asyncio
async def test_empty_candles_do_not_update_success_timestamp_and_triggers_active_probe(feed_manager):
    """Empty candles (e.g. rate limit outage) do not update success time, triggering active probe."""
    fm, mock_feed = feed_manager

    # Initial state: never fetched
    fm._last_successful_fetch_time = 0.0

    # 1. Simulate outage: feed returns empty candles []
    mock_feed.candles_to_return = []
    candles = await fm.get_candles("RELIANCE")
    assert candles == []
    assert fm._primary_failure_count == 1
    assert fm._primary_healthy is False
    assert fm._last_successful_fetch_time == 0.0  # NOT updated!

    # 2. Health check must fall through to active probe
    mock_feed.probe_calls = 0
    mock_feed.ltp_value = 2500.0  # Probe succeeds
    health = await fm.health_check()
    assert health["check_mode"] == "active_probe"
    assert mock_feed.probe_calls == 1
    assert health["healthy"] is True
    assert health["status"] == "HEALTHY"


@pytest.mark.asyncio
async def test_consecutive_failures_trigger_degraded_and_down_status(feed_manager):
    """Consecutive failures escalate status from DEGRADED to DOWN."""
    fm, mock_feed = feed_manager

    # Complete feed outage: LTP 0 and empty candles
    mock_feed.ltp_value = 0.0
    mock_feed.candles_to_return = []

    # 1st failure
    h1 = await fm.health_check(force_probe=True)
    assert h1["healthy"] is False
    assert h1["failure_count"] == 1
    assert h1["status"] == "DEGRADED"

    # 2nd failure
    h2 = await fm.health_check(force_probe=True)
    assert h2["healthy"] is False
    assert h2["failure_count"] == 2
    assert h2["status"] == "DEGRADED"

    # 3rd failure (hits threshold >= 3)
    h3 = await fm.health_check(force_probe=True)
    assert h3["healthy"] is False
    assert h3["failure_count"] == 3
    assert h3["status"] == "DOWN"

    # Status check exposes DOWN status
    status = fm.get_status()
    assert status["status"] == "DOWN"
    assert status["primary_failures"] == 3
    assert status["primary_healthy"] is False


@pytest.mark.asyncio
async def test_engine_routes_feed_unresponsive_and_recovery_alerts():
    """Engine triggers feed_unresponsive alert on >= 3 failures, and feed_recovered alert on restoration."""
    await init_db()

    mock_feed = MockWatchdogFeed()
    feed_manager = FeedManager(primary=mock_feed, backup=None)

    risk_config = settings.get("risk", default={})
    sizing_config = settings.get("position_sizing", default={})
    capital_config = settings.get("capital", default={})
    partial_config = settings.get("partial_booking", default={})
    total_capital = resolve_total_capital(config=settings)

    async def repo_getter():
        session = async_session_factory()
        return Repository(session)

    session_manager = SessionManager(repo_getter)
    daily_risk = DailyRiskManager(risk_config, total_capital=total_capital)
    risk_engine = RiskEngine(risk_config)
    position_sizer = PositionSizer(sizing_config, capital_config)
    partial_booker = PartialBooker(partial_config)
    strategy_registry = StrategyRegistry()
    strategy_registry.discover()
    regime_detector = RegimeDetector()
    adaptive_manager = AdaptiveManager(config=settings.get("strategy_activation", default={}), registry=strategy_registry, regime_detector=regime_detector)
    performance_tracker = PerformanceTracker()
    kronos_scanner = KronosScanner()
    alert_manager = AlertManager(telegram_bot=TelegramBot(bot_token="", chat_id=""), config=settings, ws_manager=ws_manager)

    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=risk_engine,
        position_sizer=position_sizer,
        partial_booker=partial_booker,
        daily_risk_manager=daily_risk,
        broker_factory=BrokerFactory,
        feed_manager=feed_manager,
        session_manager=session_manager,
        market_hours=MarketHours(),
        ws_manager=ws_manager,
        strategy_registry=strategy_registry,
        adaptive_manager=adaptive_manager,
        regime_detector=regime_detector,
        performance_tracker=performance_tracker,
        kronos_scanner=kronos_scanner,
        alert_manager=alert_manager,
    )
    engine.feed = feed_manager
    engine.is_running = True

    routed_alerts = []
    engine.alert_manager.route_alert = lambda at, data: routed_alerts.append((at, data))

    # Simulate 3 consecutive feed failures
    mock_feed.ltp_value = 0.0
    mock_feed.candles_to_return = []

    for _ in range(3):
        await engine._update_market_context()

    # Verify feed_unresponsive alert dispatched
    feed_alerts = [a for a in routed_alerts if a[0] == "feed_alert"]
    assert len(feed_alerts) >= 1
    assert feed_alerts[0][1]["type"] == "feed_unresponsive"
    assert feed_alerts[0][1]["severity"] == "CRITICAL"
    assert feed_alerts[0][1]["action"] == "FEED_DEGRADED"

    # Status check exposes feed_health
    status = await engine.get_status()
    assert "feed_health" in status
    assert status["feed_health"]["status"] == "DOWN"
    assert status["feed_health"]["primary_failures"] >= 3

    # Now restore feed health
    mock_feed.ltp_value = 2500.0
    mock_feed.candles_to_return = [{"timestamp": datetime.now(IST).isoformat(), "open": 2500.0, "high": 2510.0, "low": 2490.0, "close": 2505.0, "volume": 1000}]
    routed_alerts.clear()

    await engine._update_market_context()

    # Verify symmetric feed_recovered alert dispatched
    recovery_alerts = [a for a in routed_alerts if a[0] == "feed_alert"]
    assert len(recovery_alerts) == 1
    assert recovery_alerts[0][1]["type"] == "feed_recovered"
    assert recovery_alerts[0][1]["severity"] == "INFO"
    assert recovery_alerts[0][1]["action"] == "FEED_RESTORED"

    # Verify status is HEALTHY
    status_recovered = await engine.get_status()
    assert status_recovered["feed_health"]["status"] == "HEALTHY"
    assert status_recovered["feed_health"]["primary_healthy"] is True


@pytest.mark.asyncio
async def test_frozen_feed_detection_during_market_hours():
    """Valid data with stalled timestamp/price for >= 5 consecutive checks triggers FROZEN status & alert.

    feeds.feed_manager.datetime is patched to always return 11:00 IST so the
    is_after_opening gate (requires now > 09:30) is deterministic regardless
    of when the test suite is executed.
    """
    from unittest.mock import patch
    from datetime import datetime as _real_datetime

    # Fixed mid-session wall time: 11:00 IST -- always satisfies is_after_opening gate
    _FIXED_NOW = _real_datetime(2026, 8, 22, 11, 0, 0, tzinfo=IST)

    class _PatchedDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return _FIXED_NOW if tz is not None else _FIXED_NOW.replace(tzinfo=None)

    with patch("feeds.feed_manager.datetime", _PatchedDatetime):
        mock_feed = MockWatchdogFeed()
        mock_market_hours = MagicMock(spec=MarketHours)
        mock_market_hours.is_market_open.return_value = True

        fm = FeedManager(primary=mock_feed, backup=None, market_hours=mock_market_hours)

        # Freeze probe response: identical timestamp and identical price
        frozen_ts = "2026-08-22T10:45:00+05:30"
        mock_feed.candles_to_return = [
            {"timestamp": frozen_ts, "open": 24850.0, "high": 24860.0, "low": 24840.0, "close": 24855.0, "volume": 5000}
        ]
        mock_feed.ltp_value = 24855.0

        # Check 1: Initial baseline observation
        h1 = await fm.health_check(force_probe=True)
        assert h1["healthy"] is True
        assert h1["frozen"] is False
        assert h1["consecutive_frozen_checks"] == 0

        # Checks 2 to 5: Stalled checks 1 through 4
        for i in range(1, 5):
            h = await fm.health_check(force_probe=True)
            assert h["healthy"] is True
            assert h["frozen"] is False
            assert h["consecutive_frozen_checks"] == i

        # Check 6: Hits 5th consecutive stalled check (>= 5 threshold) -> status becomes FROZEN
        h6 = await fm.health_check(force_probe=True)
        assert h6["healthy"] is False
        assert h6["frozen"] is True
        assert h6["status"] == "FROZEN"
        assert h6["consecutive_frozen_checks"] >= 5

        # Status export exposes FROZEN
        status = fm.get_status()
        assert status["status"] == "FROZEN"
        assert status["frozen"] is True

        # Check 7: Tick advancement clears frozen state
        mock_feed.candles_to_return = [
            {"timestamp": "2026-08-22T10:46:00+05:30", "open": 24855.0, "high": 24865.0, "low": 24850.0, "close": 24860.0, "volume": 5200}
        ]
        mock_feed.ltp_value = 24860.0

        h7 = await fm.health_check(force_probe=True)
        assert h7["healthy"] is True
        assert h7["frozen"] is False
        assert h7["status"] == "HEALTHY"
        assert h7["consecutive_frozen_checks"] == 0


@pytest.mark.asyncio
async def test_advancing_timestamp_with_flat_price_does_not_trigger_frozen():
    """Flat price with advancing bar timestamps (e.g. low-volatility period) does NOT trigger FROZEN."""
    mock_feed = MockWatchdogFeed()
    mock_market_hours = MagicMock(spec=MarketHours)
    mock_market_hours.is_market_open.return_value = True

    fm = FeedManager(primary=mock_feed, backup=None, market_hours=mock_market_hours)

    base_time = datetime(2026, 8, 22, 10, 0, tzinfo=IST)

    # 10 consecutive checks where price is strictly FLAT (24850.0) but timestamp advances every check
    for i in range(10):
        current_bar_ts = (base_time + timedelta(minutes=5 * i)).isoformat()
        mock_feed.candles_to_return = [
            {"timestamp": current_bar_ts, "open": 24850.0, "high": 24850.0, "low": 24850.0, "close": 24850.0, "volume": 1000}
        ]
        mock_feed.ltp_value = 24850.0

        h = await fm.health_check(force_probe=True)
        assert h["healthy"] is True
        assert h["frozen"] is False
        assert h["status"] == "HEALTHY"
        assert h["consecutive_frozen_checks"] == 0


@pytest.mark.asyncio
async def test_frozen_feed_ignored_outside_market_hours():
    """Identical prices outside market hours (or pre-market) do NOT trigger FROZEN status."""
    mock_feed = MockWatchdogFeed()
    mock_market_hours = MagicMock(spec=MarketHours)
    mock_market_hours.is_market_open.return_value = False  # Market closed

    fm = FeedManager(primary=mock_feed, backup=None, market_hours=mock_market_hours)

    frozen_ts = "2026-08-22T08:30:00+05:30"
    mock_feed.candles_to_return = [
        {"timestamp": frozen_ts, "open": 24850.0, "high": 24860.0, "low": 24840.0, "close": 24855.0, "volume": 5000}
    ]
    mock_feed.ltp_value = 24855.0

    # 10 consecutive checks with unchanged price outside market hours
    for _ in range(10):
        h = await fm.health_check()
        assert h["healthy"] is True
        assert h["frozen"] is False
        assert h["status"] == "HEALTHY"
        assert h["consecutive_frozen_checks"] == 0


@pytest.mark.asyncio
async def test_real_engine_get_status_and_dashboard_data_watchdog_exposure():
    """Unstarted & started real engine exposes feed_health without AttributeError or None dereference."""
    await init_db()

    risk_config = settings.get("risk", default={})
    sizing_config = settings.get("position_sizing", default={})
    capital_config = settings.get("capital", default={})
    partial_config = settings.get("partial_booking", default={})
    total_capital = resolve_total_capital(config=settings)

    async def repo_getter():
        session = async_session_factory()
        return Repository(session)

    session_manager = SessionManager(repo_getter)
    daily_risk = DailyRiskManager(risk_config, total_capital=total_capital)
    risk_engine = RiskEngine(risk_config)
    position_sizer = PositionSizer(sizing_config, capital_config)
    partial_booker = PartialBooker(partial_config)
    strategy_registry = StrategyRegistry()
    strategy_registry.discover()
    regime_detector = RegimeDetector()
    adaptive_manager = AdaptiveManager(config=settings.get("strategy_activation", default={}), registry=strategy_registry, regime_detector=regime_detector)
    performance_tracker = PerformanceTracker()
    kronos_scanner = KronosScanner()
    alert_manager = AlertManager(telegram_bot=TelegramBot(bot_token="", chat_id=""), config=settings, ws_manager=ws_manager)

    real_feed_manager = FeedManager()

    engine = UltraBotEngine(
        config=settings,
        repository_getter=repo_getter,
        error_engine=ErrorEngine(),
        risk_engine=risk_engine,
        position_sizer=position_sizer,
        partial_booker=partial_booker,
        daily_risk_manager=daily_risk,
        broker_factory=BrokerFactory,
        feed_manager=real_feed_manager,
        session_manager=session_manager,
        market_hours=MarketHours(),
        ws_manager=ws_manager,
        strategy_registry=strategy_registry,
        adaptive_manager=adaptive_manager,
        regime_detector=regime_detector,
        performance_tracker=performance_tracker,
        kronos_scanner=kronos_scanner,
        alert_manager=alert_manager,
    )

    # 1. Unstarted engine status check
    status = await engine.get_status()
    assert "feed_health" in status
    assert status["feed_health"] is not None
    assert status["feed_health"]["primary"] == "yahoo_historical"
    assert "primary_healthy" in status["feed_health"]
    assert "frozen" in status["feed_health"]

    # 2. Dashboard data check
    dashboard = await engine.get_dashboard_data()
    assert "feed_health" in dashboard
    assert dashboard["feed_health"] is not None
    assert dashboard["feed_health"]["primary"] == "yahoo_historical"



@pytest.mark.asyncio
async def test_health_probe_bypasses_ttl_cache():
    """RISK_CRITICAL: health_check() active probe must bypass the YahooHistoricalFeed TTL cache.

    Pre-populate the cache with a stale candle for the exact probe key (^NSEI:5m:1).
    Without force_refresh=True the probe would silently return the cached entry and
    yfinance would NOT be called.  With the fix, yfinance IS called on every probe.
    This test should FAIL on the old code and PASS after the force_refresh=True fix.
    """
    import time as _time
    import pandas as pd
    from unittest.mock import patch
    from feeds.yahoo_historical import YahooHistoricalFeed

    timestamps = pd.date_range(start="2026-08-23 10:30", periods=1, freq="5min", tz="Asia/Kolkata")
    fresh_df = pd.DataFrame(
        {"Open": [24900.0], "High": [24910.0], "Low": [24890.0], "Close": [24905.0], "Volume": [50000]},
        index=timestamps,
    )

    with patch("yfinance.Ticker") as mock_ticker_cls:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = fresh_df
        mock_ticker_cls.return_value = mock_ticker

        real_yahoo_feed = YahooHistoricalFeed(cache_ttl_seconds=60.0)

        # Pre-populate cache with a stale candle for the exact probe key (^NSEI:5m:1)
        stale_candle = {
            "timestamp": "2026-08-23T09:15:00+05:30",
            "open": 24000.0, "high": 24010.0, "low": 23990.0, "close": 24005.0, "volume": 1000,
        }
        probe_symbol_yahoo = "^NSEI"
        cache_key = f"{probe_symbol_yahoo}:5m:1"
        real_yahoo_feed._cache[cache_key] = {"candles": [stale_candle], "timestamp": _time.time()}

        # Sanity: without force_refresh the cache IS served (proves the cache is hot)
        cached = await real_yahoo_feed.get_candles("^NSEI", timeframe="5m", count=1)
        assert cached[0]["close"] == 24005.0, "Pre-condition: stale cache entry should be returned"
        assert mock_ticker.history.call_count == 0, "Pre-condition: yfinance not called on cache hit"

        # Wire FeedManager that forces the active probe path
        mock_market_hours = MagicMock(spec=MarketHours)
        mock_market_hours.is_market_open.return_value = True

        fm = FeedManager(
            primary=real_yahoo_feed,
            backup=None,
            market_hours=mock_market_hours,
            watchdog_interval_seconds=0.0,
        )

        health = await fm.health_check(force_probe=True)

        assert mock_ticker.history.call_count >= 1, (
            "health_check() probe must bypass the TTL cache with force_refresh=True; "
            "yfinance was NOT called — a cached entry was returned instead."
        )
        assert health["healthy"] is True
        assert health["check_mode"] == "active_probe"

