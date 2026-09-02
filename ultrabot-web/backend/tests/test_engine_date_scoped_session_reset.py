import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
from core.engine import UltraBotEngine, EngineState

@pytest.mark.asyncio
async def test_engine_start_new_day_session_resets_all_state():
    """Verify that starting the engine for a genuinely new day (same_day_session=None)

    resets pending_opportunities, invalidated_opportunities, telemetry counters, and rejections.
    """
    engine = MagicMock(spec=UltraBotEngine)
    engine.state = EngineState.STOPPED
    engine.session_id = None
    engine.feed_manager = None
    engine._errors_count = 0
    
    # Pre-populate dirty state from yesterday
    engine.pending_opportunities = {"opp-yesterday": {"id": "opp-yesterday", "symbol": "INFY"}}
    engine.invalidated_opportunities = [{"id": "opp-inval-yesterday", "symbol": "TCS"}]
    engine._recent_scan_telemetry = [{"symbol": "INFY", "status": "REJECTED"}]
    engine._symbols_scanned_count = 204
    engine._signals_passed_count = 5
    engine._signals_rejected_count = 15
    engine._rejections_by_gate = {"G1": 10}
    engine._rejections_by_strategy = {"ORB": 5}
    engine.active_strategies = ["ORB", "MRF"]
    engine.current_regime = "Sideways"
    engine.mode = "paper"
    engine.broker_name = "paper"
    
    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock()
    engine.broker = mock_broker
    engine.broker_factory = MagicMock()
    engine.broker_factory.create.return_value = mock_broker
    
    engine.initial_capital = 500000.0
    engine.session_manager = MagicMock()
    engine.session_manager.get_same_day_session = AsyncMock(return_value=None)
    engine.session_manager.get_active_session = AsyncMock(return_value=None)
    engine.session_manager.create_session = AsyncMock(return_value="new-session-id-123")
    engine.config = MagicMock()
    engine.config.get_strategy_activation.return_value = {"active": ["ORB", "MRF"]}
    engine.config.get_capital_config.return_value = {"carry_forward_capital": False}
    engine.config.get_broker_config.return_value = {}
    engine.error_engine = MagicMock()
    engine.error_engine.handle_error = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()
    engine._repo_context = MagicMock()
    
    class RepoCtx:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *args):
            pass
    engine._repo_context.return_value = RepoCtx()
    
    # Mock asyncio.create_task to avoid running actual _main_loop
    with patch("asyncio.create_task", return_value=MagicMock()):
        res = await UltraBotEngine.start(engine, mode="paper", broker_name="paper")
        
    assert res["status"] == "started"
    assert engine.session_id == "new-session-id-123"
    assert engine.pending_opportunities == {}
    # invalidated_opportunities must be a DICT (opportunity_id -> data): the
    # invalidation paths assign by key. A list here raised TypeError on the
    # first opportunity invalidation of every fresh session (live 2026-08-28).
    assert engine.invalidated_opportunities == {}
    assert isinstance(engine.invalidated_opportunities, dict)
    assert engine._recent_scan_telemetry == []
    assert engine._symbols_scanned_count == 0
    assert engine._signals_passed_count == 0
    assert engine._signals_rejected_count == 0
    assert engine._rejections_by_gate == {}
    assert engine._rejections_by_strategy == {}


@pytest.mark.asyncio
async def test_engine_start_same_day_resume_preserves_state():
    """Verify that starting the engine for a same-day restart preserves

    existing opportunities, telemetry, and rejection counts.
    """
    engine = MagicMock(spec=UltraBotEngine)
    engine.state = EngineState.STOPPED
    engine.session_id = None
    engine.feed_manager = None
    engine._errors_count = 0
    
    # Pre-populate state from earlier today
    engine.pending_opportunities = {"opp-today": {"id": "opp-today", "symbol": "RELIANCE"}}
    engine.invalidated_opportunities = [{"id": "opp-inval-today", "symbol": "BPCL"}]
    engine._recent_scan_telemetry = [{"symbol": "RELIANCE", "status": "PASSED"}]
    engine._symbols_scanned_count = 50
    engine._signals_passed_count = 2
    engine._signals_rejected_count = 3
    engine._rejections_by_gate = {"G7_VIXFilter": 2}
    engine._rejections_by_strategy = {"MB": 1}
    engine.active_strategies = ["ORB", "MRF"]
    engine.current_regime = "Sideways"
    engine.mode = "paper"
    engine.broker_name = "paper"
    
    mock_broker = MagicMock()
    mock_broker.authenticate = AsyncMock()
    engine.broker = mock_broker
    engine.broker_factory = MagicMock()
    engine.broker_factory.create.return_value = mock_broker
    
    engine.initial_capital = 500000.0
    engine.session_manager = MagicMock()
    same_day_sess = {
        "session_id": "same-day-session-456",
        "mode": "paper",
        "broker": "paper",
        "initial_capital": 500000.0,
    }
    engine.session_manager.get_same_day_session = AsyncMock(return_value=same_day_sess)
    engine.session_manager.get_active_session = AsyncMock(return_value=same_day_sess)
    engine.session_manager.resume_session = AsyncMock()
    engine.session_manager.recover_state = AsyncMock(return_value={
        "current_regime": "Sideways",
        "vix": 14.5,
        "nifty_price": 24500.0,
        "active_strategies": ["ORB", "MRF"],
    })
    engine.config = MagicMock()
    engine.config.get_strategy_activation.return_value = {"active": ["ORB", "MRF"]}
    engine.config.get_broker_config.return_value = {}
    engine.error_engine = MagicMock()
    engine.error_engine.handle_error = AsyncMock()
    engine._broadcast = AsyncMock()
    engine._route_alert = AsyncMock()
    engine._repo_context = MagicMock()
    
    class RepoCtx:
        async def __aenter__(self):
            return None
        async def __aexit__(self, *args):
            pass
    engine._repo_context.return_value = RepoCtx()
    
    with patch("asyncio.create_task", return_value=MagicMock()):
        res = await UltraBotEngine.start(engine, mode="paper", broker_name="paper")
        
    assert res["status"] == "started"
    assert engine.session_id == "same-day-session-456"
    assert len(engine.pending_opportunities) == 1
    assert "opp-today" in engine.pending_opportunities
    assert len(engine.invalidated_opportunities) == 1
    assert len(engine._recent_scan_telemetry) == 1
    assert engine._symbols_scanned_count == 50
    assert engine._signals_passed_count == 2
    assert engine._signals_rejected_count == 3
    assert engine._rejections_by_gate == {"G7_VIXFilter": 2}
    assert engine._rejections_by_strategy == {"MB": 1}
