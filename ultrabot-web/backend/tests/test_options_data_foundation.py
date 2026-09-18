"""Comprehensive tests for Phase 2: F&O Data Foundation.

Validates:
- Fyers Option Chain API payload with greeks=1.
- OptionChainFetcher parser extracting all Greeks (delta, gamma, theta, vega, iv), PCR, and Max Pain.
- Black-Scholes Greeks verification and Theta-Budget scenario modeling.
- OptionSnapshot database model persistence and repository retrieval.
- OptionChainRecorder background ingestion.
"""
import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from brokers.fyers import FyersBroker
from options.option_chain import OptionChainFetcher
from options.greeks import GreeksCalculator
from options.option_recorder import OptionChainRecorder
from db.migrations import Base
from db.repository import Repository


@pytest_asyncio.fixture
async def async_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_fyers_get_option_chain_includes_greeks_param():
    """Verify that FyersBroker.get_option_chain passes greeks=1 in payload."""
    broker = FyersBroker.__new__(FyersBroker)
    broker._get_client = MagicMock()
    mock_client = MagicMock()
    broker._get_client.return_value = mock_client
    broker._call = AsyncMock(return_value={"s": "ok", "data": {"optionsChain": []}})

    res = await broker.get_option_chain("NIFTY", strike_count=15, greeks=1)
    assert broker._call.called
    call_args = broker._call.call_args[0]
    payload = call_args[2]
    assert payload["symbol"] == "NSE:NIFTY50-INDEX"
    assert payload["strikecount"] == 15
    assert payload["greeks"] == 1


def test_option_chain_parser_extracts_all_greeks_pcr_max_pain():
    """Verify option_chain parser extracts all 5 Greeks, PCR, and Max Pain."""
    raw_fyers_response = {
        "s": "ok",
        "data": {
            "optionsChain": [
                {
                    "symbol": "NSE:NIFTY26SEP24500CE",
                    "strike_price": 24500.0,
                    "option_type": "CE",
                    "ltp": 250.0,
                    "oi": 10000,
                    "volume": 50000,
                    "bid": 249.5,
                    "ask": 250.5,
                    "iv": 0.145,
                    "delta": 0.52,
                    "gamma": 0.0008,
                    "theta": -12.5,
                    "vega": 18.2,
                    "spot_price": 24510.0,
                    "expiry": 1790409600,
                },
                {
                    "symbol": "NSE:NIFTY26SEP24500PE",
                    "strike_price": 24500.0,
                    "option_type": "PE",
                    "ltp": 240.0,
                    "oi": 12000,
                    "volume": 45000,
                    "bid": 239.5,
                    "ask": 240.5,
                    "iv": 0.148,
                    "delta": -0.48,
                    "gamma": 0.0008,
                    "theta": -11.8,
                    "vega": 18.0,
                    "spot_price": 24510.0,
                    "expiry": 1790409600,
                },
                {
                    "symbol": "NSE:NIFTY26SEP24600CE",
                    "strike_price": 24600.0,
                    "option_type": "CE",
                    "ltp": 180.0,
                    "oi": 15000,
                    "volume": 30000,
                    "iv": 0.142,
                    "delta": 0.38,
                    "gamma": 0.0007,
                    "theta": -10.5,
                    "vega": 16.5,
                    "expiry": 1790409600,
                },
                {
                    "symbol": "NSE:NIFTY26SEP24600PE",
                    "strike_price": 24600.0,
                    "option_type": "PE",
                    "ltp": 310.0,
                    "oi": 8000,
                    "volume": 20000,
                    "iv": 0.150,
                    "delta": -0.62,
                    "gamma": 0.0007,
                    "theta": -13.0,
                    "vega": 16.8,
                    "expiry": 1790409600,
                },
            ],
            "expiryData": [{"expiry": 1790409600, "date": "25-Sep-2026"}],
        },
    }

    fetcher = OptionChainFetcher(broker=None)
    parsed = fetcher.parse_fyers_chain(raw_fyers_response, symbol="NIFTY")

    assert parsed["symbol"] == "NIFTY"
    assert parsed["spot_price"] == 24510.0
    assert parsed["atm_strike"] == 24500.0
    assert len(parsed["calls"]) == 2
    assert len(parsed["puts"]) == 2

    # Verify Greeks presence
    atm_ce = parsed["calls"][0]
    assert atm_ce["delta"] == 0.52
    assert atm_ce["gamma"] == 0.0008
    assert atm_ce["theta"] == -12.5
    assert atm_ce["vega"] == 18.2
    assert atm_ce["iv"] == 0.145

    # Verify Total OI and PCR:
    # Total CE OI = 10000 + 15000 = 25000
    # Total PE OI = 12000 + 8000 = 20000
    # PCR = 20000 / 25000 = 0.8
    assert parsed["total_ce_oi"] == 25000
    assert parsed["total_pe_oi"] == 20000
    assert parsed["pcr"] == 0.8
    assert parsed["max_pain"] in (24500.0, 24600.0)


def test_black_scholes_verification_and_scenario_simulation():
    """Verify Black-Scholes Greeks calculation, Greek verification, and Theta-Budget gate."""
    calc = GreeksCalculator(risk_free_rate=0.07)

    # 1. Theoretical calculations for ATM Call
    S = 25000.0
    K = 25000.0
    T = 7.0 / 365.0  # 7 days to expiry
    sigma = 0.14     # 14% IV

    greeks = calc.all_greeks(S, K, T, sigma, option_type="CE")
    assert 0.45 <= greeks["delta"] <= 0.55
    assert greeks["gamma"] > 0
    assert greeks["theta"] < 0
    assert greeks["vega"] > 0
    assert greeks["theoretical_price"] > 0

    # 2. Broker Greek verification (comparing broker vs theoretical)
    broker_greeks = {
        "delta": greeks["delta"] + 0.01,
        "gamma": greeks["gamma"],
        "theta": greeks["theta"],
        "vega": greeks["vega"],
    }
    verif = calc.verify_greeks(broker_greeks, greeks, tolerance=0.20)
    assert verif["valid"] is True

    # Large divergence test
    bad_broker_greeks = {"delta": 0.85, "gamma": 0.0001, "theta": -50.0, "vega": 5.0}
    bad_verif = calc.verify_greeks(bad_broker_greeks, greeks, tolerance=0.20)
    assert bad_verif["valid"] is False

    # 3. Simulate P&L on +100 point spot move over 0.25 day (intraday)
    sim = calc.simulate_pnl_move(
        S=S, K=K, T=T, sigma=sigma,
        spot_move_points=100.0, days_held=0.25, option_type="CE"
    )
    assert sim["expected_pnl_per_share"] > 0
    assert sim["new_spot"] == 25100.0

    # 4. Theta-Budget Gate Check
    # Expected move 120 points on 0.50 delta = 60 points gain.
    # Daily theta = -15 points. Intraday (0.5 day) = 7.5 points theta decay.
    # Transaction cost = 1.0 point.
    # Net edge = 60 - 7.5 - 1.0 = 51.5 points. Coverage ratio = 60 / 8.5 = 7.0x (PASS)
    tb_pass = calc.check_theta_budget(
        expected_move_points=120.0,
        delta=0.50,
        daily_theta=-15.0,
        round_trip_cost_per_share=1.0,
        holding_fraction_of_day=0.5,
    )
    assert tb_pass["passed"] is True
    assert tb_pass["net_edge"] > 40.0

    # Small move failing theta budget (move 10 points * 0.5 delta = 5 pts < theta 7.5 + cost 1.0)
    tb_fail = calc.check_theta_budget(
        expected_move_points=10.0,
        delta=0.50,
        daily_theta=-15.0,
        round_trip_cost_per_share=1.0,
        holding_fraction_of_day=0.5,
    )
    assert tb_fail["passed"] is False


@pytest.mark.asyncio
async def test_option_snapshot_repository_persistence(async_session):
    """Verify storing and querying OptionSnapshot records in SQLite database."""
    repo = Repository(async_session)

    chain_mock = [
        {"symbol": "NSE:NIFTY26SEP24500CE", "strike": 24500.0, "option_type": "CE", "ltp": 250.0, "delta": 0.51},
        {"symbol": "NSE:NIFTY26SEP24500PE", "strike": 24500.0, "option_type": "PE", "ltp": 240.0, "delta": -0.49},
    ]

    snapshot = await repo.create_option_snapshot(
        underlying_symbol="NIFTY",
        spot_price=24505.5,
        expiry="25-Sep-2026",
        atm_strike=24500.0,
        pcr=1.12,
        max_pain=24500.0,
        total_ce_oi=500000,
        total_pe_oi=560000,
        tier="tradable",
        chain_data=chain_mock,
    )

    assert snapshot.id is not None
    assert snapshot.underlying_symbol == "NIFTY"
    assert snapshot.spot_price == 24505.5
    assert snapshot.pcr == 1.12

    # Query back
    snapshots = await repo.get_latest_option_snapshots("NIFTY", limit=10)
    assert len(snapshots) >= 1
    assert snapshots[0].atm_strike == 24500.0
    assert "24500CE" in snapshots[0].chain_json


@pytest.mark.asyncio
async def test_option_recorder_single_poll(async_session):
    """Verify OptionChainRecorder single poll & record pipeline."""
    mock_broker = MagicMock()
    mock_broker.get_option_chain = AsyncMock(return_value={
        "s": "ok",
        "data": {
            "optionsChain": [
                {
                    "symbol": "NSE:NIFTY26SEP24500CE",
                    "strike_price": 24500.0,
                    "option_type": "CE",
                    "ltp": 250.0,
                    "oi": 10000,
                    "spot_price": 24500.0,
                    "delta": 0.56,
                    "gamma": 0.0006,
                    "theta": -10.6,
                    "vega": 18.3,
                    "iv": 0.14,
                    "expiry": 1790409600,
                },
                {
                    "symbol": "NSE:NIFTY26SEP24500PE",
                    "strike_price": 24500.0,
                    "option_type": "PE",
                    "ltp": 250.0,
                    "oi": 11000,
                    "spot_price": 24500.0,
                    "delta": -0.44,
                    "gamma": 0.0006,
                    "theta": -17.3,
                    "vega": 18.3,
                    "iv": 0.14,
                    "expiry": 1790409600,
                },
            ],
            "expiryData": [{"expiry": 1790409600, "date": "25-Sep-2026"}],
        },
    })

    repo = Repository(async_session)
    recorder = OptionChainRecorder(
        broker=mock_broker,
        repo_getter=lambda: repo,
        symbols=["NIFTY"],
    )

    from core.market_hours import IST
    frozen_now = datetime.fromtimestamp(1790409600.0 - (15.0 * 86400.0), tz=IST)
    with patch("options.option_recorder.datetime") as mock_dt:
        mock_dt.now.return_value = frozen_now
        result = await recorder.poll_and_record_once("NIFTY", full_chain=False)

    assert result["status"] == "success"
    assert result["symbol"] == "NIFTY"
    assert result["atm_strike"] == 24500.0
    assert result["pcr"] == 1.1
    assert result["greeks_verification"]["valid"] is True
    assert result["snapshot_id"] is not None


@pytest.mark.asyncio
async def test_recorder_dynamic_broker_getter(async_session):
    """Verify OptionChainRecorder resolves broker dynamically via broker_getter."""
    mock_broker = AsyncMock()
    mock_broker.get_option_chain.return_value = {
        "s": "ok",
        "data": {
            "optionsChain": [
                {
                    "symbol": "NSE:NIFTY26SEP24500CE",
                    "strike_price": 24500.0,
                    "option_type": "CE",
                    "ltp": 250.0,
                    "oi": 10000,
                    "spot_price": 24500.0,
                    "delta": 0.50,
                    "gamma": 0.0008,
                    "theta": -12.0,
                    "vega": 18.0,
                    "iv": 0.15,
                    "expiry": 1790409600,
                }
            ],
            "expiryData": [{"expiry": 1790409600, "date": "25-Sep-2026"}],
        },
    }

    repo = Repository(async_session)
    recorder = OptionChainRecorder(
        broker=None,
        broker_getter=lambda: mock_broker,
        repo_getter=lambda: repo,
        symbols=["NIFTY"],
    )

    resolved = await recorder._resolve_broker()
    assert resolved is mock_broker

    result = await recorder.poll_and_record_once("NIFTY")
    assert result["status"] == "success"
    assert result["atm_strike"] == 24500.0


@pytest.mark.asyncio
async def test_options_api_endpoints(async_session):
    """Verify options routes: snapshots, verify-greeks, and theta-budget."""
    from api.routes.options import (
        get_option_snapshots,
        verify_greeks_endpoint,
        check_theta_budget_endpoint,
    )

    repo = Repository(async_session)
    # Seed a snapshot
    snap = await repo.create_option_snapshot(
        underlying_symbol="NIFTY",
        spot_price=24500.0,
        expiry="25-Sep-2026",
        atm_strike=24500.0,
        chain_data=[{"strike": 24500.0, "option_type": "CE", "ltp": 250.0}],
    )
    assert snap.id is not None

    # 1. Snapshots API
    res = await get_option_snapshots(symbol="NIFTY", limit=10, repo=repo, _user={})
    assert res["symbol"] == "NIFTY"
    assert res["count"] >= 1
    assert res["snapshots"][0]["spot_price"] == 24500.0

    # 2. Greeks Verification API
    greeks_res = await verify_greeks_endpoint({
        "spot_price": 24500.0,
        "strike": 24500.0,
        "tte_years": 0.02,
        "iv": 0.15,
        "option_type": "CE",
        "broker_greeks": {"delta": 0.53, "gamma": 0.00076, "theta": -16.5, "vega": 13.5},
        "tolerance": 0.25,
    }, _user={})
    assert "theoretical_greeks" in greeks_res
    assert greeks_res["verification"]["valid"] is True

    # 3. Theta Budget API
    theta_res = await check_theta_budget_endpoint({
        "expected_move_points": 100.0,
        "delta": 0.55,
        "daily_theta": 12.0,
        "round_trip_cost_per_share": 1.5,
    }, _user={})
    assert theta_res["passed"] is True
    assert theta_res["directional_gain"] == 55.0


def test_iv_rank_and_percentile_calculation():
    """Verify IV rank and percentile calculation formulas."""
    calc = GreeksCalculator()
    # Min=0.10, Max=0.30, Current=0.20 -> 50%
    ivr = calc.compute_iv_rank(current_iv=0.20, min_iv=0.10, max_iv=0.30)
    assert ivr == 50.0

    # Current=0.35 (> max) -> clamped to 100%
    assert calc.compute_iv_rank(current_iv=0.35, min_iv=0.10, max_iv=0.30) == 100.0

    # Historical: [0.12, 0.14, 0.16, 0.18, 0.22]
    # Current = 0.17 -> 3 values below out of 5 -> 60%
    ivp = calc.compute_iv_percentile(current_iv=0.17, historical_ivs=[0.12, 0.14, 0.16, 0.18, 0.22])
    assert ivp == 60.0


@pytest.mark.asyncio
async def test_repository_iv_rank_and_pruning(async_session):
    """Verify repository IV rank/percentile resolution and snapshot pruning."""
    repo = Repository(async_session)

    # Seed 3 snapshots with different ATM IVs
    for i, iv_val in enumerate([0.12, 0.18, 0.24]):
        await repo.create_option_snapshot(
            underlying_symbol="NIFTY",
            spot_price=24500.0,
            expiry="25-Sep-2026",
            atm_strike=24500.0,
            chain_data=[{"strike": 24500.0, "option_type": "CE", "iv": iv_val}],
        )

    res = await repo.get_iv_rank_and_percentile("NIFTY", current_iv=0.18, lookback_days=90)
    assert res["symbol"] == "NIFTY"
    assert res["min_iv"] == 0.12
    assert res["max_iv"] == 0.24
    assert res["iv_rank"] == 50.0
    assert res["samples_count"] >= 3

    # Prune snapshots older than 1 day (should not delete fresh ones from today)
    deleted = await repo.prune_option_snapshots(keep_days=1)
    assert deleted == 0


@pytest.mark.asyncio
async def test_recorder_health_and_session_cleanup(async_session):
    """Verify OptionChainRecorder get_health() and defensive session closing."""
    mock_broker = AsyncMock()
    mock_broker.get_option_chain.return_value = {
        "s": "ok",
        "data": {
            "optionsChain": [
                {
                    "symbol": "NSE:NIFTY26SEP24500CE",
                    "strike_price": 24500.0,
                    "option_type": "CE",
                    "ltp": 250.0,
                    "oi": 10000,
                    "spot_price": 24500.0,
                    "delta": 0.53,
                    "gamma": 0.00076,
                    "theta": -16.5,
                    "vega": 13.5,
                    "iv": 0.15,
                    "expiry": 1790409600,
                },
                {
                    "symbol": "NSE:NIFTY26SEP24500PE",
                    "strike_price": 24500.0,
                    "option_type": "PE",
                    "ltp": 250.0,
                    "oi": 11000,
                    "spot_price": 24500.0,
                    "delta": -0.47,
                    "gamma": 0.00076,
                    "theta": -15.5,
                    "vega": 13.5,
                    "iv": 0.15,
                    "expiry": 1790409600,
                },
            ],
            "expiryData": [{"expiry": 1790409600, "date": "25-Sep-2026"}],
        },
    }

    mock_repo = AsyncMock()
    recorder = OptionChainRecorder(
        broker=mock_broker,
        repo_getter=lambda: mock_repo,
        symbols=["NIFTY"],
    )

    result = await recorder.poll_and_record_once("NIFTY")
    assert result["status"] == "success"
    # Verify defensive close called
    assert mock_repo.close.called is True

    health = recorder.get_health()
    assert health["polls_count"] == 1
    assert health["rate_limit_hits_429"] == 0
    assert health["last_poll_seconds_ago"] is not None
    assert "NIFTY" in health["last_verification"]


