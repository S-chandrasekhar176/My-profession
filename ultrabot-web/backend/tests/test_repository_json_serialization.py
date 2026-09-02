"""Tests for robust JSON serialization across repository models and methods.

Verifies that passing raw Python lists, dicts, tuples, or sets into JSON-backed
SQLite Text columns (such as Signal.risk_gate_results, Trade.tags, BacktestRun.equity_curve,
DailySummary.strategies_used) never causes sqlite3.ProgrammingError.
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from db.migrations import Base
from db.repository import Repository, _from_json


@pytest_asyncio.fixture
async def async_session():
    """In-memory SQLite async session for repository tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_signal_with_list_risk_gate_results(async_session):
    """Signal.risk_gate_results passed as a raw Python list must serialize to JSON string without sqlite3 error."""
    repo = Repository(async_session)

    list_gate_results = [
        {"gate": "G1", "name": "MaxPositions", "passed": True},
        {"gate": "G3", "name": "MaxPositionSize", "passed": True},
        {"gate": "G5", "name": "MaxDailyLoss", "passed": False, "block_reason": "Daily limit reached"},
    ]
    dict_signal_data = {"rsi": 32.5, "vwap": 2450.0}

    signal = await repo.create_signal(
        symbol="RELIANCE",
        direction="LONG",
        strategy="MRF",
        confidence=0.85,
        risk_gate_results=list_gate_results,
        signal_data=dict_signal_data,
        extra_unknown_kwarg="should_be_filtered",
    )

    assert signal is not None
    assert isinstance(signal.risk_gate_results, str)
    deserialized = _from_json(signal.risk_gate_results)
    assert isinstance(deserialized, list)
    assert len(deserialized) == 3
    assert deserialized[2]["gate"] == "G5"

    # Update with new list
    updated = await repo.update_signal(
        signal.id,
        risk_gate_results=[{"gate": "ALL", "passed": True}],
    )
    assert updated is not None
    assert isinstance(updated.risk_gate_results, str)
    assert _from_json(updated.risk_gate_results) == [{"gate": "ALL", "passed": True}]


@pytest.mark.asyncio
async def test_create_trade_with_list_tags_and_dict_extra(async_session):
    """Trade.tags passed as a list/set and extra as dict must serialize cleanly."""
    repo = Repository(async_session)

    trade = await repo.create_trade(
        symbol="TCS",
        direction="LONG",
        strategy="ORB",
        entry_price=3500.0,
        quantity=10,
        tags=["intraday", "breakout", "high_vol"],
        extra={"broker_order_id": "ORD12345", "fees_breakdown": {"stt": 10.5}},
    )

    assert trade is not None
    assert isinstance(trade.tags, str)
    assert _from_json(trade.tags) == ["intraday", "breakout", "high_vol"]
    assert _from_json(trade.extra)["broker_order_id"] == "ORD12345"


@pytest.mark.asyncio
async def test_create_backtest_run_with_nested_json_structures(async_session):
    """BacktestRun parameters, results, equity_curve must accept dicts and lists."""
    repo = Repository(async_session)

    run = await repo.create_backtest_run(
        strategy="MRF",
        symbol="INFY",
        start_date="2026-01-01",
        end_date="2026-02-01",
        parameters={"rsi_period": 14, "std_mult": 2.0},
        results={"win_rate": 65.5, "profit_factor": 1.8},
        equity_curve=[100000.0, 102500.0, 101800.0, 105000.0],
    )

    assert run is not None
    assert isinstance(run.parameters, str)
    assert isinstance(run.equity_curve, str)
    assert _from_json(run.parameters)["rsi_period"] == 14
    assert _from_json(run.equity_curve) == [100000.0, 102500.0, 101800.0, 105000.0]


@pytest.mark.asyncio
async def test_create_daily_summary_with_strategies_and_sector_pnl(async_session):
    """DailySummary strategies_used (list) and sector_pnl (dict) serialize correctly."""
    repo = Repository(async_session)

    summary = await repo.create_daily_summary(
        date="2026-08-22",
        total_trades=5,
        net_pnl=12500.0,
        strategies_used=["MRF", "ORB", "VWAP_MOMENTUM"],
        sector_pnl={"IT": 8500.0, "BANK": 4000.0},
        starting_capital=500000.0,
        ending_capital=512500.0,
    )

    assert summary is not None
    assert isinstance(summary.strategies_used, str)
    assert _from_json(summary.strategies_used) == ["MRF", "ORB", "VWAP_MOMENTUM"]
    assert _from_json(summary.sector_pnl) == {"IT": 8500.0, "BANK": 4000.0}
