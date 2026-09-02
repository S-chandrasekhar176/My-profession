import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

import pytest
from core.engine import UltraBotEngine
from brokers.paper_broker import PaperBroker

@pytest.mark.asyncio
async def test_close_position_real_paper_broker_flattens_and_restores_capital():
    """Verify engine._close_position() executes a SELL transaction_type on real PaperBroker,

    ensuring the position returns to flat (0 qty / closed) and capital is credited.
    """
    initial_cap = 100000.0
    broker = PaperBroker(initial_capital=initial_cap)
    
    # 1. Open a real LONG position in TCS: 10 qty @ 3500
    buy_res = await broker.place_order(
        symbol="TCS",
        exchange="NSE",
        transaction_type="BUY",
        quantity=10,
        price=3500.0,
        order_type="MARKET",
    )
    assert buy_res["success"] is True
    assert "TCS" in broker.positions
    assert broker.positions["TCS"]["quantity"] == 10
    assert broker.positions["TCS"]["direction"] == "LONG"
    assert broker.positions["TCS"]["status"] == "OPEN"
    cap_after_buy = broker.capital
    assert cap_after_buy < initial_cap

    # Mock engine dependencies for _close_position
    engine = MagicMock(spec=UltraBotEngine)
    engine.broker = broker
    engine.session_id = "test-session"
    engine._errors_count = 0
    engine.config = MagicMock()
    engine.config.get_fees_config.return_value = {"brokerage_per_order": 20.0}
    engine.daily_risk = None
    engine.error_engine = MagicMock()
    engine.error_engine.handle_error = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()
    
    repo = MagicMock()
    repo.update_position = AsyncMock()
    repo.update_trade = AsyncMock()
    repo.get_trade = AsyncMock(return_value=None)
    repo.get_trade_by_position = AsyncMock(return_value=None)
    repo.close_position = AsyncMock()
    
    engine._repo_context = MagicMock()
    
    class RepoCtx:
        async def __aenter__(self):
            return repo
        async def __aexit__(self, exc_type, exc, tb):
            pass
            
    engine._repo_context.return_value = RepoCtx()
    
    position = MagicMock(
        id="pos-real-1",
        trade_id="trade-real-1",
        symbol="TCS",
        direction="LONG",
        quantity=10,
        entry_price=3500.0,
    )
    
    # Bind the real UltraBotEngine._close_position method to engine
    engine._close_position = UltraBotEngine._close_position.__get__(engine, UltraBotEngine)
    
    # 2. Execute exit via engine._close_position at 3600.0 (+100 profit per share)
    await engine._close_position(
        position=position,
        exit_price=3600.0,
        close_reason="target_hit",
        pnl_amount=1000.0,
        pnl_pct=2.86,
    )
    
    # 3. Assertions on real PaperBroker state:
    pos = broker.positions.get("TCS")
    assert pos is not None
    assert pos["status"] == "CLOSED", f"Expected position to be CLOSED, but got: {pos}"
    assert pos["quantity"] == 0, f"Expected position quantity 0, but got: {pos['quantity']}"
    
    assert broker.capital > cap_after_buy
    assert broker.capital > 100500.0
