"""
UltraBot Web - Main Application Entry Point
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from db.database import init_db, async_session_factory
from db.repository import Repository
from errors.error_engine import ErrorEngine
from risk.risk_engine import RiskEngine
from risk.daily_risk_manager import DailyRiskManager
from risk.position_sizer import PositionSizer
from risk.partial_booker import PartialBooker
from fees.nse_fee_calculator import NSEFeeCalculator
from brokers.factory import BrokerFactory
from feeds.yahoo_historical import YahooHistoricalFeed
from feeds.feed_manager import FeedManager
from core.engine import UltraBotEngine
from core.market_hours import MarketHours
from core.session_manager import SessionManager
from scanner.kronos.kronos_scanner import KronosScanner
from strategies.registry import StrategyRegistry
from strategies.adaptive_manager import AdaptiveManager
from strategies.regime_detector import RegimeDetector
from strategies.performance_tracker import PerformanceTracker
from notifications.telegram_bot import TelegramBot
from notifications.alert_manager import AlertManager

from api.dependencies import set_engine, set_repository
from api.routes import (
    auth,
    dashboard,
    engine as engine_routes,
    trades,
    strategies,
    watchlist,
    risk as risk_routes,
    backtest,
    brokers,
    opportunities,
    notifications,
    errors,
    settings_api,
    scanner,
    candles,
    analytics,
)
from api.websocket import ws_manager, router as ws_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────
    logger.info("UltraBot Web starting...")

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Config dicts
    risk_config = settings.get_risk_config()
    capital_config = settings.get_capital_config()
    sizing_config = settings.get_position_sizing_config()
    partial_booking_config = settings.get_partial_booking_config()
    fees_config = settings.get_fees_config()
    notif_config = settings.get_notifications_config()
    strategy_activation = {
        "activation_map": settings._raw_config.get("strategy_activation", {})
    }

    total_capital = float(capital_config.get("virtual_capital", 100000))

    # ── Create components (matching actual constructor signatures) ──

    # ErrorEngine is a singleton with no constructor args
    error_engine = ErrorEngine()

    # NSEFeeCalculator(brokerage_per_order: float)
    brokerage = float(fees_config.get("brokerage_per_order", 20))
    fee_calculator = NSEFeeCalculator(brokerage_per_order=brokerage)

    # MarketHours() – uses NSE defaults
    market_hours = MarketHours()

    # Async callable that returns a Repository for a new DB session
    async def repo_getter():
        session = async_session_factory()
        return Repository(session)


    # SessionManager(repo_getter: Callable)
    session_manager = SessionManager(repo_getter)

    # DailyRiskManager(config: Dict, total_capital: float)
    daily_risk = DailyRiskManager(risk_config, total_capital=total_capital)

    # RiskEngine(config: Dict[str, Any])
    risk_engine = RiskEngine(risk_config)

    # PositionSizer(config: Dict, capital_config: Dict)
    position_sizer = PositionSizer(sizing_config, capital_config)

    # PartialBooker(config: Dict)
    partial_booker = PartialBooker(partial_booking_config)

    # FeedManager(primary, backup) — P1: when a VALID Fyers daily token is
    # stored, the Fyers 1-minute REST feed becomes primary (fresh 1m bars
    # aggregated to 5m for strategies) with Yahoo as automatic backup
    # (FeedManager switches after 3 consecutive primary failures). Without a
    # valid Fyers token the wiring is unchanged (Yahoo-only).
    from feeds.fyers_candles import build_fyers_candle_feed

    yahoo_feed = YahooHistoricalFeed()
    fyers_feed = await build_fyers_candle_feed(repo_getter)
    if fyers_feed is not None:
        feed_manager = FeedManager(primary=fyers_feed, backup=yahoo_feed)
        logger.info("FeedManager: primary=Fyers 1m Realtime, backup=Yahoo")
    else:
        feed_manager = FeedManager(primary=yahoo_feed, backup=None)

    # Strategy components
    strategy_registry = StrategyRegistry()
    strategy_registry.discover()

    regime_detector = RegimeDetector()

    # AdaptiveManager(config=None, registry=None, regime_detector=None)
    adaptive_manager = AdaptiveManager(
        config=strategy_activation,
        registry=strategy_registry,
        regime_detector=regime_detector,
    )

    # PerformanceTracker(repository=None, persist_interval=50)
    performance_tracker = PerformanceTracker()

    # KronosScanner(weights=None)
    kronos_scanner = KronosScanner()

    # Notifications & Alerts
    telegram_bot = TelegramBot(
        bot_token=notif_config.get("telegram_bot_token", ""),
        chat_id=str(notif_config.get("telegram_chat_id", "")),
    )
    alert_manager = AlertManager(
        telegram_bot=telegram_bot,
        config=settings,
        ws_manager=ws_manager,
    )

    # Configure ErrorEngine callbacks
    async def ws_broadcast_callback(payload):
        await ws_manager.broadcast(payload.get("type", "error"), payload)

    async def telegram_error_callback(msg_or_dict):
        if isinstance(msg_or_dict, dict):
            await alert_manager.route_alert("error_alert", msg_or_dict)
        else:
            await alert_manager.route_alert("error_alert", {"what_happened": str(msg_or_dict)})

    error_engine.set_ws_callback(ws_broadcast_callback)
    error_engine.set_telegram_callback(telegram_error_callback)
    error_engine.set_db_session_getter(repo_getter)

    # Inject repo getter into risk engine (needed by G13)
    risk_engine.set_repository(repo_getter)

    # BrokerFactory is used statically – no instance needed
    # The engine calls BrokerFactory.create(...) internally

    # ── Create the engine ──
    eng = UltraBotEngine(
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
    eng.fee_calculator = fee_calculator

    # Set dependencies for API routes
    set_engine(eng)

    # Store on app state for route access
    app.state.engine = eng
    app.state.error_engine = error_engine
    app.state.ws_manager = ws_manager
    app.state.fee_calculator = fee_calculator
    app.state.alert_manager = alert_manager
    app.state.telegram_bot = telegram_bot

    # Start Market Lifecycle Scheduler
    from core.scheduler import MarketLifecycleScheduler
    market_scheduler = MarketLifecycleScheduler(engine=eng, repository_getter=repo_getter)
    market_scheduler.start()
    app.state.scheduler = market_scheduler

    # Late-start catch-up: APScheduler cron jobs never backfill missed runs,
    # so if the backend boots mid-market on a fresh trading day (e.g. 10:30
    # AM), today's 08:45 pre-market init (Top-10 watchlist generation + daily
    # risk counter reset) would be skipped entirely. Run it now — guarded
    # internally to no-op when the day is already in progress (a session or
    # closed trades exist). Background task so app startup is not blocked
    # by market-data fetches during watchlist building.
    catchup_task = asyncio.create_task(market_scheduler.run_startup_catchup())
    app.state.scheduler_catchup_task = catchup_task

    # HOTFIX #8 (live 2026-09-01): crash-aware engine auto-resume.
    # Resilience drill proved that a process kill leaves the session record in
    # status="running" (graceful stops write status="stopped", completed days
    # write "completed"). On boot, if a SAME-DAY session is still "running",
    # the engine restarts itself with the recorded mode/broker — otherwise a
    # crashed live bot stays down (SLs unenforced) until a human notices.
    # User intent is preserved: explicitly stopped sessions are NOT resumed.
    # Logic lives in core/auto_resume.py (unit-tested); started as a task so
    # app startup is never blocked.
    from core.auto_resume import auto_resume_if_crashed

    async def _auto_resume_task() -> None:
        await auto_resume_if_crashed(eng, settle_delay=5.0)

    auto_resume_task = asyncio.create_task(_auto_resume_task())
    app.state.auto_resume_task = auto_resume_task

    # -- v0.4.10: Interactive Telegram (two-way) --------------
    # Opportunity cards with Approve/Reject/Skip buttons, commands
    # (/status /positions /pnl /pause /resume) and canary alerts.
    # Uses the SAME engine decision path as the web dashboard;
    # only the configured chat_id is honored.
    if notif_config.get("telegram_interactive_enabled", False):
        from notifications.telegram_interactive import InteractiveTelegramBot

        interactive_tg = InteractiveTelegramBot(
            telegram_bot=telegram_bot,
            engine=eng,
            repo_getter=repo_getter,
            notif_config=notif_config,
        )
        interactive_tg.start()
        app.state.telegram_interactive = interactive_tg

    logger.info("UltraBot Web started")
    logger.info("Market status: %s", market_hours.get_market_status())

    yield

    # ── Shutdown ─────────────────────────────────────────────
    catchup_task = getattr(app.state, "scheduler_catchup_task", None)
    if catchup_task is not None and not catchup_task.done():
        catchup_task.cancel()
    if hasattr(app.state, "scheduler"):
        app.state.scheduler.stop()
    if hasattr(app.state, "telegram_interactive"):
        await app.state.telegram_interactive.stop()
    if eng.state.value != "stopped":
        await eng.stop()
    logger.info("UltraBot Web stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS
allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()] if allowed_origins_env else ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(engine_routes.router)
app.include_router(trades.router)
app.include_router(strategies.router)
app.include_router(watchlist.router)
app.include_router(risk_routes.router)
app.include_router(backtest.router)
app.include_router(brokers.router)
app.include_router(opportunities.router)
app.include_router(notifications.router)
app.include_router(errors.router)
app.include_router(settings_api.router)
app.include_router(scanner.router)
app.include_router(candles.router)
app.include_router(analytics.router)
app.include_router(ws_router)


@app.get("/")
async def root():
    return {"app": "UltraBot Web", "version": settings.app_version, "status": "running"}


@app.get("/health")
@app.get("/api/health")
async def health():
    engine_status = "stopped"
    db_status = "ok"
    feed_status = "ok"
    broker_status = "unknown"
    
    try:
        if hasattr(app.state, "engine") and app.state.engine is not None:
            engine_status = app.state.engine.state.value
            if app.state.engine.broker is not None:
                broker_status = app.state.engine.broker.get_name() if hasattr(app.state.engine.broker, "get_name") else "connected"
    except Exception:
        pass

    try:
        from db.database import async_session_factory
        from sqlalchemy import text
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"error: {str(e)}"

    return {
        "status": "healthy" if db_status == "ok" else "degraded",
        "app": settings.app_name,
        "version": settings.app_version,
        "db": "connected" if db_status == "ok" else "disconnected",
        "database": db_status,
        "engine": engine_status,
        "broker": broker_status,
        "feed": feed_status,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.app_host, port=settings.app_port)
