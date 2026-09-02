"""Tests for Session Recovery & Same-Day Restart Handling.

Verifies:
1. Genuinely new day creates a new session row.
2. Same-day restart recovers existing session_id and preserves initial_capital and regime.
3. Restart resumes the MOST RECENT active session of the day and adopts ITS capital
   (corrected 2026-08-28: was "first session" which let a stale pre-market test
   session shadow every restart and permanently disable same-day resume).
4. Date-scoping verification: open positions and closed trades from earlier today remain visible.
5. A stale stopped first session with a DIFFERENT broker never blocks resume of the
   current matching session (no repeated mismatch-close loop).
"""
import pytest
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from core.session_manager import SessionManager
from core.engine import UltraBotEngine

IST = ZoneInfo("Asia/Kolkata")


class FakeRepo:
    """In-memory mock repository simulating DB sessions and trades."""

    def __init__(self):
        self.sessions = []
        self.trades = []
        self.positions = []

    async def create_session(self, date_str=None, engine_state=None, metadata_json=None):
        today = date_str or datetime.now(IST).date().isoformat()
        session_obj = MagicMock()
        session_obj.id = f"sess-{len(self.sessions) + 1}"
        session_obj.date = today
        session_obj.status = "running"
        session_obj.engine_state = engine_state or {}
        session_obj.metadata_json = metadata_json or {}
        session_obj.created_at = datetime.now(IST).isoformat()
        session_obj.updated_at = datetime.now(IST).isoformat()
        self.sessions.append(session_obj)
        return session_obj

    async def get_session(self, session_id):
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None

    async def get_latest_session(self):
        return self.sessions[-1] if self.sessions else None

    async def get_first_session_by_date(self, date_str):
        matches = [s for s in self.sessions if s.date == date_str]
        return matches[0] if matches else None

    async def get_latest_session_by_date(self, date_str):
        matches = [s for s in self.sessions if s.date == date_str]
        return matches[-1] if matches else None

    async def get_sessions_by_date(self, date_str):
        return [s for s in self.sessions if s.date == date_str]

    async def update_session(self, session_id, **kwargs):
        session = await self.get_session(session_id)
        if session:
            for k, v in kwargs.items():
                setattr(session, k, v)
        return session

    async def save_session_state(self, session_id, state):
        return await self.update_session(session_id, engine_state=state)

    async def get_active_watchlist(self):
        return []

    async def get_todays_trades(self):
        today = datetime.now(IST).date().isoformat()
        return [t for t in self.trades if getattr(t, "date", today) == today]

    async def get_open_positions(self):
        return [p for p in self.positions if getattr(p, "status", "OPEN") == "OPEN"]


def create_test_engine(repo, session_mgr):
    mock_config = MagicMock()
    mock_config.get_broker_config.return_value = {}
    mock_config.get_strategy_activation.return_value = {"active": ["momentum"]}
    mock_config.get_capital_config.return_value = {"virtual_capital": 500000.0}

    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock(return_value={"success": True})
    mock_broker.get_positions = AsyncMock(return_value=[])

    mock_broker_factory = MagicMock()
    mock_broker_factory.create.return_value = mock_broker

    mock_feed_mgr = MagicMock()
    mock_feed_mgr.connect = AsyncMock()

    engine = UltraBotEngine(
        config=mock_config,
        repository_getter=lambda: repo,
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=mock_broker_factory,
        feed_manager=mock_feed_mgr,
        session_manager=session_mgr,
    )
    engine._route_alert = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._main_loop = AsyncMock()
    return engine


@pytest.mark.asyncio
async def test_fresh_start_creates_new_session():
    """On a clean day with no prior session, start() creates a new session row."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)
    engine = create_test_engine(repo, session_mgr)

    res = await engine.start(mode="paper", broker_name="paper", initial_capital=500000.0)
    assert res["status"] == "started"
    assert engine.session_id == "sess-1"
    assert len(repo.sessions) == 1
    assert repo.sessions[0].status == "running"


@pytest.mark.asyncio
async def test_same_day_restart_resumes_existing_session_and_capital():
    """Restarting engine on the same day re-attaches to sess-1 and restores capital/regime."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)

    # First morning session started with 480,000 and Bullish_Trend regime
    first_sess_id = await session_mgr.create_session(
        mode="paper",
        broker="paper",
        initial_capital=480000.0,
        metadata={"mode": "paper", "broker": "paper"},
    )
    assert first_sess_id == "sess-1"

    mock_engine_prior = MagicMock()
    mock_engine_prior.mode = "paper"
    mock_engine_prior.broker_name = "paper"
    mock_engine_prior.broker = MagicMock()
    mock_engine_prior.broker.get_positions = AsyncMock(return_value=[])
    mock_engine_prior.initial_capital = 480000.0
    mock_engine_prior.current_regime = "Bullish_Trend"
    mock_engine_prior.vix = 13.5
    mock_engine_prior.nifty_price = 24500.0
    mock_engine_prior.active_strategies = ["trend_following"]
    mock_engine_prior.pending_opportunities = {}
    mock_engine_prior.daily_risk = None
    await session_mgr.save_state("sess-1", mock_engine_prior)

    # Simulate crash / stop
    await session_mgr.close_session("sess-1", final_capital=480000.0, status="stopped")

    # Start new engine instance (mid-day restart)
    engine = create_test_engine(repo, session_mgr)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.session_id == "sess-1"
    assert len(repo.sessions) == 1  # No duplicate session created
    assert repo.sessions[0].status == "running"
    assert engine.initial_capital == 480000.0
    assert engine.current_regime == "Bullish_Trend"
    assert engine.vix == 13.5


@pytest.mark.asyncio
async def test_multiple_restarts_resume_latest_session_and_its_capital():
    """Multiple sessions in one day: restart resumes the MOST RECENT active
    session and adopts ITS capital (the latest session is the canonical
    day-state; rewinding to the first session's capital would reset the
    day's P&L anchoring)."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)

    # Earlier session of the day started at 500,000 (now superseded)
    await session_mgr.create_session("paper", "paper", initial_capital=500000.0)
    # Latest session of the day running at 450,000
    s2 = await session_mgr.create_session("paper", "paper", initial_capital=450000.0)

    engine = create_test_engine(repo, session_mgr)
    await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    # Must resume s2 (the LATEST session created today) with its capital
    assert engine.session_id == s2
    assert engine.initial_capital == 450000.0


@pytest.mark.asyncio
async def test_stale_first_session_never_shadows_current_session():
    """Regression (live 2026-08-28): a stale pre-market session with a
    DIFFERENT broker used to be re-closed on every restart, permanently
    disabling same-day resume for the actually-running session. The engine
    must anchor to the LATEST session and resume it cleanly."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)

    # Stale pre-market test session (paper/paper, already stopped)
    s_stale = await session_mgr.create_session("paper", "paper", initial_capital=100000.0)
    await session_mgr.close_session(s_stale, final_capital=100000.0, status="stopped")

    # The real trading session (paper/yahoofinance, 500k) — currently active
    s_real = await session_mgr.create_session("paper", "yahoofinance", initial_capital=500000.0)

    engine = create_test_engine(repo, session_mgr)
    await engine.start(mode="paper", broker_name="yahoofinance", initial_capital=None)

    # Resumes the real session — NOT the stale one, no mismatch-close noise
    assert engine.session_id == s_real
    assert engine.initial_capital == 500000.0


@pytest.mark.asyncio
async def test_date_scoped_trades_continuity_across_restart():
    """Open positions and closed trades from earlier in the day remain visible to resumed engine."""
    repo = FakeRepo()
    today = datetime.now(IST).date().isoformat()

    pos = MagicMock(id="pos-1", symbol="RELIANCE", status="OPEN", entry_price=1300.0, quantity=10)
    trade = MagicMock(id="tr-1", symbol="INFY", status="CLOSED", net_pnl=1500.0, date=today)
    repo.positions.append(pos)
    repo.trades.append(trade)

    session_mgr = SessionManager(repo_getter=lambda: repo)
    await session_mgr.create_session("paper", "paper", initial_capital=500000.0)

    open_pos = await repo.get_open_positions()
    todays_trades = await repo.get_todays_trades()

    assert len(open_pos) == 1
    assert open_pos[0].symbol == "RELIANCE"
    assert len(todays_trades) == 1
    assert todays_trades[0].net_pnl == 1500.0


@pytest.mark.asyncio
async def test_mode_mismatch_refuses_to_resume():
    """If morning session was mode='paper' and new start() requests mode='live', do not resume paper state."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)

    # Morning session created in paper mode
    s1 = await session_mgr.create_session("paper", "paper", initial_capital=500000.0)

    engine = create_test_engine(repo, session_mgr)
    # Start in live mode
    res = await engine.start(mode="live", broker_name="angel_one", initial_capital=1000000.0)

    assert res["status"] == "started"
    # Must NOT resume s1
    assert engine.session_id != s1
    assert engine.mode == "live"
    assert engine.broker_name == "angel_one"
    assert len(repo.sessions) == 2

    # Verify s1 was closed as stopped (superseded)
    old_session = await repo.get_session(s1)
    assert old_session.status == "stopped"

    # Verify exactly one running session exists for today
    running_sessions = [s for s in repo.sessions if s.status == "running"]
    assert len(running_sessions) == 1
    assert running_sessions[0].id == engine.session_id


@pytest.mark.asyncio
async def test_completed_session_not_auto_resumed():
    """If morning session was formally completed (EOD run), do not resume it as running."""
    repo = FakeRepo()
    session_mgr = SessionManager(repo_getter=lambda: repo)

    # Completed session from earlier today
    s1 = await session_mgr.create_session("paper", "paper", initial_capital=500000.0)
    await session_mgr.close_session(s1, final_capital=510000.0, status="completed")

    engine = create_test_engine(repo, session_mgr)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=500000.0)

    assert res["status"] == "started"
    # Must create a new session rather than reopening the completed one
    assert engine.session_id != s1
    assert len(repo.sessions) == 2
