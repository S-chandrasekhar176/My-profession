"""Tests for engine feed outage and rate-limit degradation behavior.

Verifies:
1. Injected feed network timeouts / HTTP 429 errors are handled gracefully.
2. Market context defaults safely to neutral regime and fallback VIX.
3. Watchlist scan records NO_SETUP telemetry with 'Insufficient candles (0/20)' without crashing.
4. Position management continues operating safely without throwing uncaught exceptions.
"""
import pytest
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

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


class OutageFailingFeed(BaseFeed):
    """A feed simulating complete network failure / Yahoo rate-limit (HTTP 429)."""

    def __init__(self):
        super().__init__()
        self.call_count = 0

    async def connect(self) -> bool:
        return True

    def get_name(self) -> str:
        return "outage_sim_feed"

    async def subscribe(self, symbols: List[str]):
        pass

    async def unsubscribe(self, symbols: List[str]):
        pass

    async def disconnect(self):
        pass

    def is_connected(self) -> bool:
        return True

    async def get_ltp(self, symbol: str) -> Optional[float]:
        self.call_count += 1
        raise ConnectionError(f"HTTP 429 Rate Limit Exceeded for {symbol}")

    async def get_latest_price(self, symbol: str) -> Optional[float]:
        self.call_count += 1
        return None

    async def get_candles(
        self, symbol: str, timeframe: str = "5min", count: int = 100
    ) -> List[Dict[str, Any]]:
        self.call_count += 1
        return []


@pytest.mark.asyncio
async def test_engine_gracefully_degrades_during_feed_outage():
    """Live engine must safely record NO_SETUP telemetry and keep operating under total feed outage."""
    await init_db()

    async def repo_getter():
        session = async_session_factory()
        return Repository(session)

    risk_config = settings.get("risk", default={})
    sizing_config = settings.get("position_sizing", default={})
    capital_config = settings.get("capital", default={})
    partial_config = settings.get("partial_booking", default={})

    total_capital = resolve_total_capital(config=settings)
    error_engine = ErrorEngine()
    market_hours = MarketHours()
    session_manager = SessionManager(repo_getter)
    daily_risk = DailyRiskManager(risk_config, total_capital=total_capital)
    risk_engine = RiskEngine(risk_config)
    position_sizer = PositionSizer(sizing_config, capital_config)
    partial_booker = PartialBooker(partial_config)

    failing_feed = OutageFailingFeed()
    feed_manager = FeedManager(primary=failing_feed, backup=None)

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
    engine.watchlist = ["RELIANCE", "TCS", "INFY"]
    engine.is_running = True

    # Deterministic isolation: remove any watchlist rows left in the shared
    # dev DB by earlier tests (e.g. test_vix_staleness_safety seeds RELIANCE)
    # so _scan_watchlist() exercises the engine.watchlist fallback path.
    # DELETE (not deactivate) so later re-seeding tests don't hit the
    # UNIQUE(symbol) constraint.
    _cleanup_repo = await repo_getter()
    try:
        for _item in await _cleanup_repo.get_active_watchlist():
            await _cleanup_repo.delete_watchlist_item(_item.id)
        await _cleanup_repo.session.commit()
    finally:
        await _cleanup_repo.close()

    # 1. Update market context with dead feed (sets fallback default)
    engine.vix_updated_at = datetime.now(IST)  # Keep VIX non-critically stale to test candle degradation path
    await engine._update_market_context()
    assert engine.vix >= 15.0

    # 2. Watchlist scan with dead feed (candles fail)
    engine.vix_updated_at = datetime.now(IST)
    engine.vix_critical_stale = False
    await engine._scan_watchlist()

    # 3. Telemetry inspection
    assert len(engine._recent_scan_telemetry) >= 3
    for event in engine._recent_scan_telemetry:
        assert event["status"] == "NO_SETUP"
        assert "Insufficient candles (0/20)" in event["reason"]

    # 4. Position management during feed outage
    sample_pos = Position(
        id="pos-sim-1",
        session_id="session-sim-1",
        symbol="RELIANCE",
        direction="LONG",
        strategy="MRF",
        quantity=10,
        entry_price=2500.0,
        current_price=2500.0,
        stop_loss=2450.0,
        target=2600.0,
        status="OPEN",
        entry_time=datetime.now(timezone.utc).isoformat(),
    )
    engine.positions = {"RELIANCE": sample_pos}
    await engine._manage_all_positions()
    assert sample_pos.status == "OPEN"
    assert sample_pos.current_price == 2500.0
