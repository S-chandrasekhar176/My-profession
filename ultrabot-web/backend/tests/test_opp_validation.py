import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock

IST = ZoneInfo("Asia/Kolkata")
from core.engine import UltraBotEngine


@pytest.mark.asyncio
async def test_opportunity_validation_on_confirmation():
    """Verify UltraBotEngine confirm_opportunity validates TTL, target hit, SL breach, and price mismatch."""
    config = MagicMock()
    config.get_risk_config.return_value = {
        "price_mismatch_threshold_pct": 0.5,
        "opportunity_ttl_seconds": 120,
    }
    config.get_capital_config.return_value = {"virtual_capital": 100000}
    config.get_broker_config.return_value = {}
    config.get_partial_booking_config.return_value = {}
    config.get_fees_config.return_value = {"brokerage_per_order": 20}

    repo_getter = AsyncMock()
    repo_mock = AsyncMock()
    repo_getter.return_value = repo_mock

    feed_manager = MagicMock()
    broker_factory = MagicMock()
    session_manager = MagicMock()
    ws_manager = MagicMock()
    ws_manager.broadcast = AsyncMock()

    engine = UltraBotEngine(
        config=config,
        repository_getter=repo_getter,
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=MagicMock(),
        daily_risk_manager=MagicMock(),
        broker_factory=broker_factory,
        feed_manager=feed_manager,
        session_manager=session_manager,
        ws_manager=ws_manager,
    )

    feed_mock = MagicMock()
    engine.feed = feed_mock

    now = datetime.now(IST)

    # 1. Target hit rejection test
    feed_mock.get_latest_price = AsyncMock(return_value=1420.0)
    engine.pending_opportunities["opp_target"] = {
        "id": "opp_target",
        "symbol": "RELIANCE",
        "direction": "BUY",
        "entry_price": 1400.0,
        "stop_loss": 1390.0,
        "target": 1412.0,
        "quantity": 10,
        "strategy": "Breakout",
        "created_at": now.isoformat(),
    }
    res_target = await engine.confirm_opportunity("opp_target")
    assert res_target["status"] == "rejected"
    assert "Target" in res_target["reason"]

    # 2. Stop loss breach rejection test
    feed_mock.get_latest_price = AsyncMock(return_value=1620.0)
    engine.pending_opportunities["opp_sl"] = {
        "id": "opp_sl",
        "symbol": "HDFCBANK",
        "direction": "BUY",
        "entry_price": 1640.0,
        "stop_loss": 1628.5,
        "target": 1660.0,
        "quantity": 10,
        "strategy": "Breakout",
        "created_at": now.isoformat(),
    }
    res_sl = await engine.confirm_opportunity("opp_sl")
    assert res_sl["status"] == "rejected"
    assert "Stop loss" in res_sl["reason"]

    # 3. TTL expired rejection test
    feed_mock.get_latest_price = AsyncMock(return_value=4100.0)
    engine.pending_opportunities["opp_expired"] = {
        "id": "opp_expired",
        "symbol": "TCS",
        "direction": "BUY",
        "entry_price": 4100.0,
        "stop_loss": 4050.0,
        "target": 4180.0,
        "quantity": 10,
        "strategy": "Breakout",
        "created_at": (now - timedelta(seconds=150)).isoformat(),
    }
    res_expired = await engine.confirm_opportunity("opp_expired")
    assert res_expired["status"] == "rejected"
    assert "expired" in res_expired["reason"]
