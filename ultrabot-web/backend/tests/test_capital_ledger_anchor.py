"""Tests for Self-Healing Carry-Forward: Anchor boot capital to trades ledger truth.

Verifies:
a. Clean chain: prior ending == base + SUM(net) -> boot uses ending, NO warning.
b. Re-anchored row (exact Sep-21 scenario): prior ending = 500,688.01,
   SUM(net) = -2,771.07 -> boot initial == 497,228.93, warning logged.
c. No summary rows at all + trades exist -> initial == base + SUM(net), NOT bare base.
d. No trades -> current behavior unchanged, no warning.
"""
import pytest
import logging
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from core.session_manager import SessionManager
from core.engine import UltraBotEngine

IST = ZoneInfo("Asia/Kolkata")


class FakeTrade:
    def __init__(self, net_pnl: float):
        self.net_pnl = net_pnl


class FakeDailySummary:
    def __init__(self, date_str: str, ending_capital: float, net_pnl: float = 0.0):
        self.date = date_str
        self.ending_capital = ending_capital
        self.net_pnl = net_pnl


class FakeRepoWithTradesAndSummaries:
    """Mock repository with daily summaries and trades support."""

    def __init__(self):
        self.sessions = []
        self.summaries = []
        self.trades = []

    async def create_session(self, date_str=None, engine_state=None, metadata_json=None):
        today = date_str or datetime.now(IST).date().isoformat()
        session_obj = MagicMock()
        session_obj.id = f"sess-{len(self.sessions) + 1}"
        session_obj.date = today
        session_obj.status = "running"
        session_obj.engine_state = engine_state or {}
        session_obj.metadata_json = metadata_json or {}
        self.sessions.append(session_obj)
        return session_obj

    async def get_first_session_by_date(self, date_str):
        matches = [s for s in self.sessions if s.date == date_str]
        return matches[0] if matches else None

    async def get_sessions_by_date(self, date_str):
        return [s for s in self.sessions if s.date == date_str]

    async def update_session(self, session_id, **kwargs):
        for s in self.sessions:
            if s.id == session_id:
                for k, v in kwargs.items():
                    setattr(s, k, v)
                return s
        return None

    async def get_latest_prior_daily_summary(self, before_date=None):
        target_date = before_date or datetime.now(IST).date().isoformat()
        priors = [s for s in self.summaries if s.date < target_date]
        priors.sort(key=lambda s: s.date, reverse=True)
        return priors[0] if priors else None

    async def get_latest_daily_summary(self):
        if not self.summaries:
            return None
        sorted_summaries = sorted(self.summaries, key=lambda s: s.date, reverse=True)
        return sorted_summaries[0]

    async def get_trade_count(self) -> int:
        return len(self.trades)

    async def get_all_time_realized_net(self) -> float:
        return float(sum(t.net_pnl for t in self.trades))

    async def get_latest_session_by_date(self, date_str):
        matches = [s for s in self.sessions if s.date == date_str]
        return matches[-1] if matches else None

    async def get_session(self, session_id):
        for s in self.sessions:
            if s.id == session_id:
                return s
        return None


def _seed_same_day_session(repo, initial_capital: float, mode: str = "paper", broker: str = "paper"):
    """Seed a non-completed session for TODAY so start() takes the resume path."""
    today = datetime.now(IST).date().isoformat()
    engine_state = {
        "mode": mode,
        "broker": broker,
        "initial_capital": initial_capital,
        "current_regime": "Sideways",
        "vix": 15.0,
        "nifty_price": 0.0,
        "open_positions": [],
        "watchlist": [],
        "daily_risk": {},
        "active_strategies": [],
        "pending_opportunities": [],
    }
    session_obj = MagicMock()
    session_obj.id = f"sess-{len(repo.sessions) + 1}"
    session_obj.date = today
    session_obj.status = "running"
    session_obj.engine_state = engine_state
    session_obj.metadata_json = {"broker": broker, "mode": mode, "initial_capital": initial_capital}
    repo.sessions.append(session_obj)
    return session_obj


def create_engine(
    repo,
    carry_forward=True,
    virtual_capital=500000.0,
):
    mock_config = MagicMock()
    mock_config.get_broker_config.return_value = {}
    mock_config.get_strategy_activation.return_value = {"active": ["momentum"]}
    mock_config.get_capital_config.return_value = {
        "virtual_capital": virtual_capital,
        "carry_forward_capital": carry_forward,
    }

    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock(return_value={"success": True})
    mock_broker.get_margin = AsyncMock(
        return_value={"available": virtual_capital, "used": 0.0, "total": virtual_capital}
    )

    mock_broker_factory = MagicMock()
    mock_broker_factory.create.return_value = mock_broker

    mock_feed_mgr = MagicMock()
    mock_feed_mgr.connect = AsyncMock()

    session_mgr = SessionManager(repo_getter=lambda: repo)

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
async def test_clean_chain_prior_ending_matches_ledger_no_warning(caplog):
    """a. Clean chain: prior ending == base + SUM(net) -> boot uses ending, NO warning."""
    repo = FakeRepoWithTradesAndSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    # base 500,000 + 688.01 net = 500,688.01 ending
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=500688.01))
    repo.trades.append(FakeTrade(net_pnl=688.01))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    with caplog.at_level(logging.WARNING):
        res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 500688.01
    assert "Boot capital reconciliation" not in caplog.text


@pytest.mark.asyncio
async def test_reanchored_row_drift_corrected_to_ledger_truth(caplog):
    """b. Re-anchored row (exact Sep-21 scenario): prior ending = 500,688.01,
    SUM(net) = -2,771.07 -> boot initial == 497,228.93, warning logged.
    """
    repo = FakeRepoWithTradesAndSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=500688.01))
    repo.trades.append(FakeTrade(net_pnl=-2771.07))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    with caplog.at_level(logging.WARNING):
        res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == pytest.approx(497228.93, abs=0.01)
    assert "Boot capital reconciliation: resolved ₹500688.01 but trades ledger implies ₹497228.93" in caplog.text


@pytest.mark.asyncio
async def test_no_summary_rows_at_all_trades_exist_anchors_to_ledger(caplog):
    """c. No summary rows at all + trades exist -> initial == base + SUM(net), NOT bare base."""
    repo = FakeRepoWithTradesAndSummaries()
    # No summaries at all
    repo.trades.append(FakeTrade(net_pnl=-2771.07))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    with caplog.at_level(logging.WARNING):
        res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == pytest.approx(497228.93, abs=0.01)
    assert "Boot capital reconciliation: resolved ₹500000.00 but trades ledger implies ₹497228.93" in caplog.text


@pytest.mark.asyncio
async def test_no_trades_current_behavior_unchanged_no_warning(caplog):
    """d. No trades -> current behavior unchanged, no warning."""
    repo = FakeRepoWithTradesAndSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=500688.01))
    # 0 trades

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    with caplog.at_level(logging.WARNING):
        res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 500688.01
    assert "Boot capital reconciliation" not in caplog.text


@pytest.mark.asyncio
async def test_repository_all_time_realized_net_sql_aggregation():
    """Verify Repository.get_all_time_realized_net and get_trade_count execute correct SQL."""
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
    from sqlalchemy.orm import sessionmaker
    from db.migrations import Base
    from db.repository import Repository

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        repo = Repository(session)
        # Empty table
        assert await repo.get_trade_count() == 0
        assert await repo.get_all_time_realized_net() == 0.0

        # Insert trades
        await repo.create_trade(symbol="IDEA", direction="BUY", strategy="ORB", entry_price=14.05, quantity=100, net_pnl=1242.40)
        await repo.create_trade(symbol="BHARATFORG", direction="BUY", strategy="ORB", entry_price=1991.90, quantity=10, net_pnl=620.09)
        await repo.create_trade(symbol="IDEA", direction="SELL", strategy="ORB", entry_price=13.70, quantity=100, net_pnl=-1551.42)

        assert await repo.get_trade_count() == 3
        expected_sum = round(1242.40 + 620.09 - 1551.42, 2)
        actual_sum = round(await repo.get_all_time_realized_net(), 2)
        assert actual_sum == pytest.approx(expected_sum, abs=0.01)

    await engine.dispose()


@pytest.mark.asyncio
async def test_same_day_resume_reconciles_boot_capital_with_ledger(caplog):
    """REGRESSION (same-day resume path): a mid-day restart restores
    initial_capital=500,688.01 from the session engine_state, but the trades
    ledger implies ₹497,228.93 (base 500,000 + SUM(net) −2,771.07). The
    reconciliation must ALSO run on the same-day resume path, override the
    restored capital, and log the drift warning.

    FAILS on the pre-fix tip (reconciliation only ran on the new-day path);
    PASSES once _reconcile_boot_capital_with_ledger() is called on resume.
    """
    repo = FakeRepoWithTradesAndSummaries()
    # No daily summaries: engine resumes the seeded session's initial_capital
    # (500,688.01) directly from engine_state via recover_state().
    _seed_same_day_session(repo, initial_capital=500688.01)
    repo.trades.append(FakeTrade(net_pnl=-2771.07))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    with caplog.at_level(logging.WARNING):
        res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == pytest.approx(497228.93, abs=0.01)
    assert (
        "Boot capital reconciliation: resolved ₹500688.01 but trades ledger implies ₹497228.93"
        in caplog.text
    )
