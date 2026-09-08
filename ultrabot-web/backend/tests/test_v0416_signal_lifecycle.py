"""v0.4.16 regression tests — user-testing feedback fixes (2026-09-08).

Covers the signal-lifecycle and PnL/quote consistency fixes from the local
Fyers-data test session:

1. confirm_opportunity (fill) must resolve the DB signal row to 'filled' —
   previously it stayed 'pending' and the restart sweep branded ACCEPTED,
   EXECUTED trades as "Pending opportunity lost on engine restart"
   (sandbox: 3 rows on 2026-09-08; UI showed the same setup in the confirmed
   AND invalidated/expired lists).
2. confirm_opportunity rejections (TTL / target-hit) must resolve the popped
   signal to 'expired' with the reason.
3. The trade_fill broadcast payload must carry opportunity_id so the web
   dashboard can mark the card confirmed when approval came from TELEGRAM.
4. get_todays_pnl must not double-count brokerage (fees already include the
   full round trip; gross − fees must equal net).
5. /api/live-quotes must report NIFTY change in POINTS and changePct in
   percent (they used to both carry the percent value).
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")

from core.engine import UltraBotEngine
from db.repository import Repository
from api.routes.candles import get_live_quotes


def _build_engine(repo_mock) -> UltraBotEngine:
    """Engine wired exactly like the established confirm-flow tests."""
    mock_config = MagicMock()
    mock_config.get_risk_config = MagicMock(
        return_value={"opportunity_ttl_seconds": 300, "price_mismatch_threshold_pct": 5.0}
    )
    mock_config.get_fees_config = MagicMock(return_value={})

    async def get_repo():
        return repo_mock

    engine = UltraBotEngine(
        config=mock_config,
        repository_getter=get_repo,
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )
    return engine


def _make_fill_engine() -> tuple[UltraBotEngine, MagicMock, MagicMock]:
    mock_broker = MagicMock()
    mock_broker.place_order = AsyncMock(
        return_value={
            "order_id": "PAPER-TEST-1",
            "status": "FILLED",
            "filled_price": 100.0,
            "filled_quantity": 10,
        }
    )

    mock_repo = MagicMock()
    mock_repo.create_trade = AsyncMock()
    mock_repo.create_position = AsyncMock()
    mock_repo.update_signal = AsyncMock()

    engine = _build_engine(mock_repo)
    engine.broker = mock_broker
    engine.vix = 15.0
    engine.current_regime = "Bull"
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()
    engine._run_risk_gates = AsyncMock(return_value={"passed": True, "all_gates": []})
    engine._calculate_position_size = AsyncMock(return_value={"quantity": 10, "position_size": 1000})
    return engine, mock_repo, mock_broker


@pytest.mark.asyncio
async def test_confirm_fill_marks_signal_filled():
    """Successful fill must update the DB signal from 'pending' to 'filled'."""
    engine, mock_repo, mock_broker = _make_fill_engine()

    opp_id = "opp-sig-lifecycle-1"
    engine.pending_opportunities[opp_id] = {
        "id": opp_id,
        "signal_id": "sig-abc-123",
        "symbol": "RELIANCE",
        "direction": "BUY",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "target": 104.0,
        "quantity": 10,
        "strategy": "ORB",
        "confidence": 0.85,
        "created_at": datetime.now(IST).isoformat(),
    }

    res = await engine.confirm_opportunity(opportunity_id=opp_id, segment="EQ")
    assert res["status"] == "filled", res

    # THE fix: signal row resolved at fill time.
    mock_repo.update_signal.assert_awaited_once()
    args, kwargs = mock_repo.update_signal.await_args
    assert args[0] == "sig-abc-123"
    assert kwargs.get("status") == "filled"

    # Cross-channel sync: broadcast payload carries the opportunity id.
    broadcast_channels = [c.args[0] for c in engine._broadcast.await_args_list]
    assert "trade" in broadcast_channels
    trade_payload = next(c.args[1] for c in engine._broadcast.await_args_list if c.args[0] == "trade")
    assert trade_payload["opportunity_id"] == opp_id


@pytest.mark.asyncio
async def test_confirm_ttl_rejection_resolves_signal_expired():
    """A TTL-expired confirm must resolve the popped signal, not leave it pending."""
    engine, mock_repo, _ = _make_fill_engine()

    opp_id = "opp-sig-lifecycle-ttl"
    engine.pending_opportunities[opp_id] = {
        "id": opp_id,
        "signal_id": "sig-ttl-456",
        "symbol": "TCS",
        "direction": "BUY",
        "entry_price": 4100.0,
        "stop_loss": 4050.0,
        "target": 4180.0,
        "quantity": 10,
        "strategy": "ORB",
        "created_at": (datetime.now(IST) - timedelta(seconds=400)).isoformat(),
    }

    res = await engine.confirm_opportunity(opportunity_id=opp_id, segment="EQ")
    assert res["status"] == "rejected"
    assert "expired" in res["reason"]

    mock_repo.update_signal.assert_awaited_once()
    args, kwargs = mock_repo.update_signal.await_args
    assert args[0] == "sig-ttl-456"
    assert kwargs.get("status") == "expired"
    assert kwargs.get("rejection_reason")


@pytest.mark.asyncio
async def test_confirm_target_rejection_resolves_signal_expired():
    """Target-hit-before-entry reject must also resolve the popped signal."""
    engine, mock_repo, mock_broker = _make_fill_engine()

    feed_mock = MagicMock()
    feed_mock.get_latest_price = AsyncMock(return_value=150.0)  # >= target 104
    engine.feed = feed_mock

    opp_id = "opp-sig-lifecycle-target"
    engine.pending_opportunities[opp_id] = {
        "id": opp_id,
        "signal_id": "sig-target-789",
        "symbol": "RELIANCE",
        "direction": "BUY",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "target": 104.0,
        "quantity": 10,
        "strategy": "ORB",
        "created_at": datetime.now(IST).isoformat(),
    }

    res = await engine.confirm_opportunity(opportunity_id=opp_id, segment="EQ")
    assert res["status"] == "rejected"

    mock_repo.update_signal.assert_awaited_once()
    args, kwargs = mock_repo.update_signal.await_args
    assert args[0] == "sig-target-789"
    assert kwargs.get("status") == "expired"


class _FakeClosedTrade:
    def __init__(self, pnl, fees, brokerage, net_pnl):
        self.status = "CLOSED"
        self.pnl = pnl
        self.fees = fees
        self.brokerage = brokerage
        self.net_pnl = net_pnl


@pytest.mark.asyncio
async def test_get_todays_pnl_does_not_double_count_brokerage():
    """total_fees must equal the fees column only — brokerage is already inside
    the round-trip fees written by the close path (v0.4.8 HF), so adding it
    again broke gross − fees = net on the dashboard (BPCL/RADICO day)."""
    repo = Repository.__new__(Repository)
    trades = [
        _FakeClosedTrade(pnl=0.0, fees=81.65, brokerage=20.0, net_pnl=-81.65),
        _FakeClosedTrade(pnl=29.64, fees=81.65, brokerage=20.0, net_pnl=-52.01),
    ]
    repo.get_trades_by_date = AsyncMock(return_value=trades)

    res = await repo.get_todays_pnl()

    assert res["total_fees"] == pytest.approx(163.30, abs=0.01)
    assert res["gross_pnl"] == pytest.approx(29.64, abs=0.01)
    assert res["net_pnl"] == pytest.approx(-133.66, abs=0.01)
    # The core accounting invariant the dashboard lost before the fix:
    assert res["gross_pnl"] - res["total_fees"] == pytest.approx(res["net_pnl"], abs=0.01)


class _FakeEngineQuotes:
    broker_name = "paper"
    nifty_price = 23662.75
    nifty_change = -0.14  # engine stores PERCENT
    banknifty_price = 0  # forces the Yahoo/cache path for BANKNIFTY
    vix = 0
    broker = None


@pytest.mark.asyncio
async def test_live_quotes_nifty_change_points_vs_pct():
    """NIFTY change must be POINTS (derived from the engine percent) and
    changePct must be the percent — previously both carried the percent."""
    res = await get_live_quotes(symbols="NIFTY", engine=_FakeEngineQuotes())
    assert res["success"] is True

    nifty = res["data"]["NIFTY"]
    assert nifty["price"] == pytest.approx(23662.75, abs=0.01)
    assert nifty["changePct"] == pytest.approx(-0.14, abs=0.001)

    # points = price − price / (1 + pct/100)
    expected_points = 23662.75 - 23662.75 / (1 - 0.14 / 100)
    assert nifty["change"] == pytest.approx(expected_points, abs=0.51)
    # A 0.14% move on ~23.6k is ~33 points — the old bug reported 0.14.
    assert abs(nifty["change"]) > 20.0
