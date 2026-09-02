"""Tests for 3-Tier VIX Staleness Tracking and Safety Fallback.

Verifies:
1. Tier 1 (Normal): Fresh VIX sets vix_updated_at, normal regime, normal signal scanning.
2. Tier 2 (Warning Staleness): Age > warning threshold applies conservative floor (22.0),
   dispatches warning alert, adapts regime to Volatile.
3. Tier 3 (Critical Staleness): Age > critical threshold marks vix_critical_stale=True,
   halts new signal generation, broadcasts HALTED telemetry, while open position management continues.
4. Recovery: Fresh VIX fetch restores normal state and un-halts scanning.
"""
import pytest
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo

from config.settings import settings
from core.capital_resolver import resolve_total_capital
from core.engine import UltraBotEngine
from errors.error_engine import ErrorEngine
from core.market_hours import MarketHours
from risk.position_sizer import PositionSizer
from risk.partial_booker import PartialBooker
from core.session_manager import SessionManager
from brokers.factory import BrokerFactory
from feeds.base import BaseFeed
from feeds.feed_manager import FeedManager
from risk.daily_risk_manager import DailyRiskManager
from risk.risk_engine import RiskEngine
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker
from scanner.kronos.kronos_scanner import KronosScanner
from notifications.alert_manager import AlertManager
from notifications.telegram_bot import TelegramBot
from api.websocket import ws_manager
from db.database import init_db, async_session_factory
from db.repository import Repository
from db.migrations import Position

IST = ZoneInfo("Asia/Kolkata")


class MockVixFeed(BaseFeed):
    """Feed that allows controlling VIX return value or failing it."""

    def __init__(self, vix_value: Optional[float] = 14.5):
        super().__init__()
        self.vix_value = vix_value
        self.candles_returned = 0

    async def connect(self) -> bool:
        return True

    def get_name(self) -> str:
        return "mock_vix_feed"

    async def subscribe(self, symbols: List[str]):
        pass

    async def unsubscribe(self, symbols: List[str]):
        pass

    async def disconnect(self):
        pass

    def is_connected(self) -> bool:
        return True

    async def get_ltp(self, symbol: str) -> Optional[float]:
        if "VIX" in symbol:
            return self.vix_value
        return 2500.0

    async def get_latest_price(self, symbol: str) -> Optional[float]:
        if "VIX" in symbol:
            return self.vix_value
        return 2500.0

    async def get_candles(
        self, symbol: str, timeframe: str = "5min", count: int = 100
    ) -> List[Dict[str, Any]]:
        if not symbol.startswith("^") and "NIFTY" not in symbol and "BANKNIFTY" not in symbol:
            self.candles_returned += 1
        now = datetime.now(IST)
        return [
            {
                "timestamp": (now - timedelta(minutes=5 * i)).isoformat(),
                "open": 2500.0,
                "high": 2510.0,
                "low": 2490.0,
                "close": 2505.0,
                "volume": 10000,
            }
            for i in range(25)
        ]


@pytest.fixture
def test_engine():
    """Create a configured UltraBotEngine test fixture."""
    risk_config = settings.get("risk", default={})
    sizing_config = settings.get("position_sizing", default={})
    capital_config = settings.get("capital", default={})
    partial_config = settings.get("partial_booking", default={})

    total_capital = resolve_total_capital(config=settings)
    error_engine = ErrorEngine()
    market_hours = MarketHours()

    async def repo_getter():
        session = async_session_factory()
        return Repository(session)

    session_manager = SessionManager(repo_getter)
    daily_risk = DailyRiskManager(risk_config, total_capital=total_capital)
    risk_engine = RiskEngine(risk_config)
    position_sizer = PositionSizer(sizing_config, capital_config)
    partial_booker = PartialBooker(partial_config)

    feed = MockVixFeed(vix_value=14.5)
    feed_manager = FeedManager(primary=feed, backup=None)

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
        error_engine=error_engine,
        risk_engine=risk_engine,
        position_sizer=position_sizer,
        partial_booker=partial_booker,
        daily_risk_manager=daily_risk,
        broker_factory=BrokerFactory,
        feed_manager=feed_manager,
        session_manager=session_manager,
        market_hours=market_hours,
        ws_manager=ws_manager,
        strategy_registry=strategy_registry,
        adaptive_manager=adaptive_manager,
        regime_detector=regime_detector,
        performance_tracker=performance_tracker,
        kronos_scanner=kronos_scanner,
        alert_manager=alert_manager,
    )
    engine.mode = "paper"
    engine.broker_name = "paper"
    engine.broker = BrokerFactory.create("paper", mode="paper")
    engine.feed = feed_manager
    engine.is_running = True
    return engine, feed


@pytest.mark.asyncio
async def test_tier1_normal_fresh_vix(test_engine):
    """Tier 1: Fresh VIX updates timestamp and allows normal signal generation."""
    await init_db()
    engine, feed = test_engine
    feed.vix_value = 14.5

    await engine._update_market_context()

    assert engine.vix == 14.5
    assert engine.vix_updated_at is not None
    assert engine.vix_critical_stale is False
    assert engine.current_regime == "Sideways"

    # Status export verification
    status = await engine.get_status()
    assert status["vix"] == 14.5
    assert status["vix_critical_stale"] is False
    assert status["vix_staleness_seconds"] is not None
    assert status["vix_staleness_seconds"] < 5.0


@pytest.mark.asyncio
async def test_startup_grace_period_no_immediate_halt(test_engine):
    """Startup grace period: engine started, vix_updated_at is None, failing fetch does not immediately halt."""
    await init_db()
    engine, feed = test_engine

    # Fresh engine startup state
    engine.vix = 15.0
    engine.vix_updated_at = None
    engine._start_time = datetime.now(IST)
    feed.vix_value = None  # Feed fails to return VIX on initial call

    routed_alerts = []
    engine.alert_manager.route_alert = lambda at, data: routed_alerts.append((at, data))

    await engine._update_market_context()

    # Must NOT be marked critical stale during startup grace period
    assert engine.vix_critical_stale is False
    assert engine.vix == 15.0  # Uses default fallback without aggressive floor
    assert len(routed_alerts) == 0  # No critical alert dispatched on startup

    # Watchlist scan must not be halted by VIX
    async with engine._repo_context() as repo:
        items = await repo.get_active_watchlist()
        if not items:
            # Idempotent seeding: the shared dev DB may already hold an
            # INACTIVE RELIANCE row (UNIQUE constraint is on symbol regardless
            # of is_active) — re-activate instead of colliding on insert.
            existing = await repo.get_watchlist_item_by_symbol("RELIANCE")
            if existing is None:
                await repo.add_watchlist_item(symbol="RELIANCE", name="Reliance Industries", is_active=True)
            else:
                await repo.update_watchlist_item(existing.id, is_active=True)
            await repo.session.commit()

    feed.candles_returned = 0
    await engine._scan_watchlist()
    assert engine.vix_critical_stale is False


@pytest.mark.asyncio
async def test_tier2_warning_staleness_applies_floor(test_engine):
    """Tier 2: When VIX age > warning threshold (360s), conservative floor is applied."""
    await init_db()
    engine, feed = test_engine

    # Simulate an earlier successful fetch 400s ago
    engine.vix = 13.0
    engine.vix_updated_at = datetime.now(IST) - timedelta(seconds=400)
    feed.vix_value = None  # Feed fails to fetch fresh VIX

    routed_alerts = []
    engine.alert_manager.route_alert = lambda at, data: routed_alerts.append((at, data))

    await engine._update_market_context()

    # VIX must be clamped to conservative floor 22.0
    assert engine.vix == 22.0
    assert engine.current_regime == "Volatile"
    assert engine.vix_critical_stale is False
    assert len(routed_alerts) == 1
    assert routed_alerts[0][0] == "risk_alert"
    assert routed_alerts[0][1]["type"] == "vix_stale_warning"
    assert routed_alerts[0][1]["applied_vix"] == 22.0


@pytest.mark.asyncio
async def test_tier3_critical_staleness_halts_new_signals(test_engine):
    """Tier 3: When VIX age > critical threshold (540s), new signal generation is halted."""
    await init_db()
    engine, feed = test_engine

    # Ensure an active watchlist item exists in repo
    async with engine._repo_context() as repo:
        items = await repo.get_active_watchlist()
        if not items:
            # Idempotent seeding: the shared dev DB may already hold an
            # INACTIVE RELIANCE row (UNIQUE constraint is on symbol regardless
            # of is_active) — re-activate instead of colliding on insert.
            existing = await repo.get_watchlist_item_by_symbol("RELIANCE")
            if existing is None:
                await repo.add_watchlist_item(symbol="RELIANCE", name="Reliance Industries", is_active=True)
            else:
                await repo.update_watchlist_item(existing.id, is_active=True)
            await repo.session.commit()

    # Simulate VIX timestamp 600 seconds ago (critically stale)
    engine.vix = 14.0
    engine.vix_updated_at = datetime.now(IST) - timedelta(seconds=600)
    feed.vix_value = None

    feed.candles_returned = 0
    await engine._scan_watchlist()

    assert engine.vix_critical_stale is True
    assert engine.vix == 22.0  # Floor applied
    # No symbol candle scans were executed because signal generation was halted
    assert feed.candles_returned == 0

    # HALTED telemetry event is recorded
    last_event = engine._recent_scan_telemetry[-1]
    assert last_event["status"] == "HALTED"
    assert "New signals halted: VIX critically stale" in last_event["reason"]


@pytest.mark.asyncio
async def test_critical_staleness_preserves_position_management(test_engine):
    """Critical VIX staleness halts new signals but still allows position SL/Target monitoring."""
    await init_db()
    engine, feed = test_engine

    engine.vix_critical_stale = True

    sample_pos = Position(
        id="pos-crit-1",
        session_id="session-crit-1",
        symbol="RELIANCE",
        direction="LONG",
        strategy="MRF",
        quantity=10,
        entry_price=2500.0,
        current_price=2500.0,
        stop_loss=2450.0,
        target=2600.0,
        status="OPEN",
        entry_time=datetime.now(IST).isoformat(),
    )
    engine.positions = {"RELIANCE": sample_pos}

    await engine._manage_all_positions()
    assert sample_pos.status == "OPEN"


@pytest.mark.asyncio
async def test_recovery_when_fresh_vix_returns(test_engine):
    """When fresh VIX returns after critical staleness, engine un-halts immediately and emits recovery alert."""
    await init_db()
    engine, feed = test_engine

    # Set critically stale
    engine.vix_critical_stale = True
    engine.vix_updated_at = datetime.now(IST) - timedelta(seconds=700)

    routed_alerts = []
    engine.alert_manager.route_alert = lambda at, data: routed_alerts.append((at, data))

    # Fresh VIX arrives
    feed.vix_value = 15.2
    await engine._update_market_context()

    assert engine.vix == 15.2
    assert engine.vix_critical_stale is False
    assert engine.current_regime == "Sideways"
    assert (datetime.now(IST) - engine.vix_updated_at).total_seconds() < 2.0

    # Verify symmetric VIX_RECOVERED alert was dispatched
    assert len(routed_alerts) == 1
    assert routed_alerts[0][0] == "risk_alert"
    assert routed_alerts[0][1]["type"] == "vix_recovered"
    assert routed_alerts[0][1]["vix"] == 15.2
    assert routed_alerts[0][1]["action"] == "RESUMED_NORMAL_OPERATIONS"
