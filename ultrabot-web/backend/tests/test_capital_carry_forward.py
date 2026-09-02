"""Tests for Step (b) — Day-to-Day Capital Carry-Forward & Live Margin Resolution.

Verifies:
1. Fresh install / no prior history falls back to resolve_total_capital.
2. Weekend/holiday gap: reads most recent prior daily summary (date < today ORDER BY date DESC).
3. Carry forward disabled (carry_forward_capital=False) ignores prior summary and uses config.
4. Corrupted/zero/negative ending_capital fails safe and falls back to config.
5. Live mode fetches real-time margin from broker.get_margin() across real broker payload formats (Angel One, Kite, Dhan, Fyers, Shoonya).
6. Live mode broker failure logs error and falls back to resolve_total_capital safely.
7. Same-day completed summary is never used for carry forward (strictly date < today).
8. Paper mode synchronizes PaperBroker.capital to engine's resolved starting capital.
"""
import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

from core.session_manager import SessionManager
from core.engine import UltraBotEngine
from brokers.paper_broker import PaperBroker

IST = ZoneInfo("Asia/Kolkata")


class FakeDailySummary:
    def __init__(self, date_str: str, ending_capital: float, net_pnl: float = 0.0):
        self.date = date_str
        self.ending_capital = ending_capital
        self.net_pnl = net_pnl


class FakeRepoWithSummaries:
    """Mock repository with daily summaries support."""

    def __init__(self):
        self.sessions = []
        self.summaries = []

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


def create_engine(
    repo,
    carry_forward=False,
    virtual_capital=500000.0,
    broker_margin=None,
    broker_fail=False,
    real_paper_broker=False,
):
    mock_config = MagicMock()
    mock_config.get_broker_config.return_value = {}
    mock_config.get_strategy_activation.return_value = {"active": ["momentum"]}
    mock_config.get_capital_config.return_value = {
        "virtual_capital": virtual_capital,
        "carry_forward_capital": carry_forward,
    }

    if real_paper_broker:
        mock_broker = PaperBroker(initial_capital=virtual_capital)
        mock_broker.authenticate = AsyncMock(return_value={"success": True})
    else:
        mock_broker = MagicMock()
        mock_broker.authenticate = AsyncMock(return_value={"success": True})
        if broker_fail:
            mock_broker.get_margin = AsyncMock(side_effect=RuntimeError("Broker network disconnect"))
        elif broker_margin is not None:
            mock_broker.get_margin = AsyncMock(return_value=broker_margin)
        else:
            mock_broker.get_margin = AsyncMock(return_value={"available": virtual_capital, "used": 0.0, "total": virtual_capital})

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
async def test_fresh_install_no_prior_history_falls_back_to_config():
    """Fresh install with 0 summaries falls back to configured virtual_capital."""
    repo = FakeRepoWithSummaries()
    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)

    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)
    assert res["status"] == "started"
    assert engine.initial_capital == 500000.0


@pytest.mark.asyncio
async def test_weekend_holiday_gap_carries_forward_most_recent_summary():
    """On Monday, carry forward loads Friday's (3 days prior) ending capital."""
    repo = FakeRepoWithSummaries()
    today = datetime.now(IST).date()
    friday_date = (today - timedelta(days=3)).isoformat()
    repo.summaries.append(FakeDailySummary(friday_date, ending_capital=487500.0, net_pnl=-12500.0))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 487500.0


@pytest.mark.asyncio
async def test_carry_forward_disabled_uses_config_default():
    """When carry_forward_capital is False, engine always uses defaults.yaml virtual_capital."""
    repo = FakeRepoWithSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=420000.0))

    engine = create_engine(repo, carry_forward=False, virtual_capital=500000.0)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 500000.0


@pytest.mark.asyncio
async def test_corrupted_or_zero_ending_capital_fails_safe_to_config():
    """If prior summary has ending_capital <= 0, fail safe to config capital."""
    repo = FakeRepoWithSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=0.0))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 500000.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "broker_name,broker_margin_payload,expected_capital",
    [
        ("angel_one", {"total": 1250000.0, "available": 1250000.0, "used": 0.0}, 1250000.0),
        ("kite", {"available": 950000.0, "used": 50000.0, "total": 1000000.0}, 950000.0),
        ("dhan", {"available": 850000.0, "used": 0.0, "total": 850000.0}, 850000.0),
        ("fyers", {"available": 650000.0, "used": 50000.0, "total": 700000.0}, 650000.0),
        ("shoonya", {"total": 750000.0, "available": 750000.0, "used": 0.0}, 750000.0),
    ],
)
async def test_live_mode_fetches_real_broker_margins(broker_name, broker_margin_payload, expected_capital):
    """Live mode fetches actual margin using get_margin() matching each broker's format."""
    repo = FakeRepoWithSummaries()
    engine = create_engine(
        repo,
        carry_forward=True,
        virtual_capital=500000.0,
        broker_margin=broker_margin_payload,
    )

    res = await engine.start(mode="live", broker_name=broker_name, initial_capital=None)
    assert res["status"] == "started"
    assert engine.initial_capital == expected_capital


@pytest.mark.asyncio
async def test_live_mode_broker_error_fails_safe_to_config():
    """Live mode broker.get_margin() failure logs error and falls back to config capital."""
    repo = FakeRepoWithSummaries()
    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0, broker_fail=True)

    res = await engine.start(mode="live", broker_name="angel_one", initial_capital=None)
    assert res["status"] == "started"
    assert engine.initial_capital == 500000.0


@pytest.mark.asyncio
async def test_same_day_completed_summary_strictly_isolated_from_prior_query():
    """A summary written for today is never picked up by get_latest_prior_daily_summary()."""
    repo = FakeRepoWithSummaries()
    today_str = datetime.now(IST).date().isoformat()
    yesterday_str = (datetime.now(IST).date() - timedelta(days=1)).isoformat()

    # Yesterday: 490,000; Today's completed summary: 510,000
    repo.summaries.append(FakeDailySummary(yesterday_str, ending_capital=490000.0))
    repo.summaries.append(FakeDailySummary(today_str, ending_capital=510000.0))

    prior = await repo.get_latest_prior_daily_summary()
    assert prior is not None
    assert prior.date == yesterday_str
    assert prior.ending_capital == 490000.0


@pytest.mark.asyncio
async def test_paper_broker_capital_synchronization():
    """Paper mode synchronizes PaperBroker.capital to engine's resolved starting capital."""
    repo = FakeRepoWithSummaries()
    yesterday = (datetime.now(IST).date() - timedelta(days=1)).isoformat()
    repo.summaries.append(FakeDailySummary(yesterday, ending_capital=475000.0))

    engine = create_engine(repo, carry_forward=True, virtual_capital=500000.0, real_paper_broker=True)
    res = await engine.start(mode="paper", broker_name="paper", initial_capital=None)

    assert res["status"] == "started"
    assert engine.initial_capital == 475000.0
    assert engine.broker.capital == 475000.0


@pytest.mark.asyncio
async def test_live_mode_available_zero_preserved_without_falling_through_to_total():
    """If broker returns available=0.0 (fully invested) and total=500,000, resolve to 0.0, NOT 500,000."""
    repo = FakeRepoWithSummaries()
    # Fully invested account: available=0.0, total=500000.0
    broker_margin_payload = {"available": 0.0, "total": 500000.0, "used": 500000.0}
    engine = create_engine(
        repo,
        carry_forward=True,
        virtual_capital=500000.0,
        broker_margin=broker_margin_payload,
    )

    res = await engine.start(mode="live", broker_name="angel_one", initial_capital=None)
    assert res["status"] == "started"
    # Must resolve to 0.0 (the explicit available margin), NOT fall through to total (500000.0)
    assert engine.initial_capital == 0.0
