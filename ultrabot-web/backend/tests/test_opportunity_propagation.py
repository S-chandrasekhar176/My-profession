"""Regression tests for opportunity propagation pipeline (WebSocket broadcast, REST endpoint, channel mapping, and payload validation)."""
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from core.engine import UltraBotEngine, EngineState
from api.websocket import WebSocketManager, VALID_CHANNELS
from config.settings import Settings


@pytest.fixture
def ws_manager():
    return WebSocketManager()


def _make_engine(ws_manager=None):
    cfg = MagicMock(spec=Settings)
    cfg.get_capital_config.return_value = {
        "virtual_capital": 500000.0,
        "max_capital_usage_pct": 90.0,
        "min_position_size": 5000.0,
        "max_per_position_pct": 25.0,
    }
    cfg.get_risk_config.return_value = {
        "vix_staleness_warning_seconds": 360,
        "vix_staleness_critical_seconds": 540,
        "vix_stale_floor": 22.0,
    }
    cfg.get_partial_booking_config.return_value = {}
    cfg.get_strategy_activation.return_value = {
        "active": ["orb", "mb", "mrf", "ptc", "sic", "trs", "vc"],
        "paused": [],
    }

    engine = UltraBotEngine(
        config=cfg,
        repository_getter=None,
        error_engine=None,
        risk_engine=None,
        position_sizer=None,
        partial_booker=None,
        daily_risk_manager=None,
        broker_factory=None,
        feed_manager=None,
        session_manager=None,
        market_hours=None,
        ws_manager=ws_manager,
    )
    engine.vix = 15.0
    engine.current_regime = "Sideways"
    return engine


class TestOpportunityChannelAndPayloadPropagation:
    def test_opportunity_channel_in_valid_channels(self):
        """Ensure both 'opportunity' and 'new_opportunity' are registered valid channels."""
        assert "opportunity" in VALID_CHANNELS
        assert "new_opportunity" in VALID_CHANNELS

    def test_resolve_channels_routes_opportunity_to_both(self, ws_manager):
        """_resolve_channels must route opportunity events to both 'new_opportunity' and 'opportunity'."""
        routes = ws_manager._resolve_channels("opportunity", {"type": "new_opportunity", "opportunity": {}})
        assert "new_opportunity" in routes
        assert "opportunity" in routes

    @pytest.mark.asyncio
    async def test_websocket_broadcast_delivery_to_subscribers(self, ws_manager):
        """Clients subscribed to either 'opportunity' or 'new_opportunity' receive the broadcast message."""
        mock_ws_opp = AsyncMock()
        mock_ws_new_opp = AsyncMock()

        await ws_manager.connect(mock_ws_opp, default_channels={"opportunity"})
        await ws_manager.connect(mock_ws_new_opp, default_channels={"new_opportunity"})

        payload = {
            "type": "new_opportunity",
            "opportunity": {
                "id": "opp-test-123",
                "symbol": "RELIANCE",
                "direction": "LONG",
                "entry_price": 2850.0,
                "sl_price": 2820.0,
                "target_price": 2910.0,
                "confidence": 0.85,
            },
        }

        await ws_manager.broadcast("opportunity", payload)

        queue_opp = ws_manager._queues[mock_ws_opp]
        queue_new_opp = ws_manager._queues[mock_ws_new_opp]

        assert not queue_opp.empty()
        assert not queue_new_opp.empty()

        msg_opp = json.loads(await queue_opp.get())
        msg_new_opp = json.loads(await queue_new_opp.get())

        assert msg_opp["channel"] == "opportunity"
        assert msg_opp["data"]["type"] == "new_opportunity"
        assert msg_opp["data"]["opportunity"]["id"] == "opp-test-123"

        assert msg_new_opp["channel"] == "opportunity"
        assert msg_new_opp["data"]["type"] == "new_opportunity"
        assert msg_new_opp["data"]["opportunity"]["symbol"] == "RELIANCE"

        await ws_manager.disconnect(mock_ws_opp)
        await ws_manager.disconnect(mock_ws_new_opp)

    @pytest.mark.asyncio
    async def test_engine_build_and_broadcast_opportunity_structure(self, ws_manager):
        """UltraBotEngine _build_opportunity constructs canonical keys and broadcasts."""
        engine = _make_engine(ws_manager=ws_manager)

        mock_ws = AsyncMock()
        await ws_manager.connect(mock_ws, default_channels={"opportunity"})

        signal = {
            "symbol": "TCS",
            "direction": "LONG",
            "entry_price": 3950.0,
            "sl_price": 3900.0,
            "target_price": 4050.0,
            "confidence": 0.82,
        }
        sizing = {"quantity": 25, "position_value": 98750.0}
        risk_result = {
            "all_gates": [
                {"name": "G1_TradingHours", "passed": True, "message": "OK"},
                {"name": "G3_PositionSize", "passed": True, "message": "OK"},
            ]
        }

        opp = engine._build_opportunity(
            signal=signal,
            strategy_name="orb",
            symbol="TCS",
            current_price=3950.0,
            sizing=sizing,
            risk_result=risk_result,
        )

        assert opp["symbol"] == "TCS"
        assert opp["direction"] == "LONG"
        assert opp["confidence"] == 0.82
        assert opp["risk_reward"] == 2.0  # (4050-3950)/(3950-3900) = 100/50 = 2.0
        assert "id" in opp

        # Broadcast via engine
        await engine._broadcast("opportunity", {"type": "new_opportunity", "opportunity": opp})

        queue = ws_manager._queues[mock_ws]
        assert not queue.empty()
        raw_msg = await queue.get()
        data = json.loads(raw_msg)
        assert data["data"]["opportunity"]["id"] == opp["id"]
        assert data["data"]["opportunity"]["symbol"] == "TCS"

        await ws_manager.disconnect(mock_ws)

    @pytest.mark.asyncio
    async def test_rest_api_get_pending_opportunities_contract(self):
        """Test that get_pending_opportunities returns running engine's pending opportunities."""
        from api.routes.opportunities import get_pending_opportunities

        engine = _make_engine()
        engine.state = EngineState.RUNNING

        opp_id = "test-opp-abc"
        engine.pending_opportunities[opp_id] = {
            "id": opp_id,
            "symbol": "INFY",
            "direction": "LONG",
            "entry_price": 1850.0,
            "stop_loss": 1820.0,
            "target": 1910.0,
            "confidence": 0.88,
        }

        res = await get_pending_opportunities(username="admin", engine=engine)
        assert len(res) == 1
        assert res[0]["id"] == opp_id
        assert res[0]["symbol"] == "INFY"
        assert res[0]["confidence"] == 0.88


class TestScanWatchlistSymbolExclusions:
    @pytest.mark.asyncio
    async def test_scan_watchlist_skips_symbol_with_open_position(self):
        """Symbol with an existing open position is skipped without running strategy scans."""
        engine = _make_engine()
        engine.state = EngineState.RUNNING
        engine.active_strategies = ["orb"]

        mock_repo = AsyncMock()
        item1 = MagicMock(symbol="RELIANCE")
        item2 = MagicMock(symbol="INFY")
        mock_repo.get_active_watchlist.return_value = [item1, item2]

        pos_rel = MagicMock(symbol="RELIANCE", status="OPEN")
        mock_repo.get_open_positions.return_value = [pos_rel]

        engine._repo_context = MagicMock()
        engine._repo_context.return_value.__aenter__.return_value = mock_repo

        engine._scan_symbol = AsyncMock()
        engine._update_market_context = AsyncMock()
        engine._validate_pending_opportunities = AsyncMock()
        engine._broadcast = AsyncMock()

        await engine._scan_watchlist()

        # _scan_symbol must be called for INFY, but NOT for RELIANCE
        engine._scan_symbol.assert_called_once()
        call_args = engine._scan_symbol.call_args[0]
        assert call_args[0] == "INFY"

        # Check telemetry recorded SKIPPED for RELIANCE
        telemetry = engine.get_scan_telemetry()
        rel_events = [e for e in telemetry["recent_events"] if e.get("symbol") == "RELIANCE"]
        assert len(rel_events) > 0
        assert rel_events[0]["status"] == "SKIPPED"
        assert rel_events[0]["gate"] == "OpenPosition"

    @pytest.mark.asyncio
    async def test_scan_watchlist_skips_symbol_with_pending_opportunity(self):
        """Symbol with an existing pending opportunity is skipped without running strategy scans."""
        engine = _make_engine()
        engine.state = EngineState.RUNNING
        engine.active_strategies = ["orb"]

        mock_repo = AsyncMock()
        item1 = MagicMock(symbol="TCS")
        item2 = MagicMock(symbol="HDFCBANK")
        mock_repo.get_active_watchlist.return_value = [item1, item2]
        mock_repo.get_open_positions.return_value = []

        engine._repo_context = MagicMock()
        engine._repo_context.return_value.__aenter__.return_value = mock_repo

        # Register pending opportunity for TCS
        engine.pending_opportunities["opp-tcs-1"] = {
            "id": "opp-tcs-1",
            "symbol": "TCS",
            "direction": "LONG",
        }

        engine._scan_symbol = AsyncMock()
        engine._update_market_context = AsyncMock()
        engine._validate_pending_opportunities = AsyncMock()
        engine._broadcast = AsyncMock()

        await engine._scan_watchlist()

        # _scan_symbol must be called for HDFCBANK, but NOT for TCS
        engine._scan_symbol.assert_called_once()
        call_args = engine._scan_symbol.call_args[0]
        assert call_args[0] == "HDFCBANK"

        # Check telemetry recorded SKIPPED for TCS
        telemetry = engine.get_scan_telemetry()
        tcs_events = [e for e in telemetry["recent_events"] if e.get("symbol") == "TCS"]
        assert len(tcs_events) > 0
        assert tcs_events[0]["status"] == "SKIPPED"
        assert tcs_events[0]["gate"] == "PendingOpportunity"

    @pytest.mark.asyncio
    async def test_scan_watchlist_scans_clean_symbol_normally(self):
        """Symbol without open positions or pending opportunities is scanned normally."""
        engine = _make_engine()
        engine.state = EngineState.RUNNING
        engine.active_strategies = ["orb"]

        mock_repo = AsyncMock()
        item = MagicMock(symbol="SBIN")
        mock_repo.get_active_watchlist.return_value = [item]
        mock_repo.get_open_positions.return_value = []

        engine._repo_context = MagicMock()
        engine._repo_context.return_value.__aenter__.return_value = mock_repo

        engine._scan_symbol = AsyncMock()
        engine._update_market_context = AsyncMock()
        engine._validate_pending_opportunities = AsyncMock()
        engine._broadcast = AsyncMock()

        await engine._scan_watchlist()

        engine._scan_symbol.assert_called_once()
        call_args, call_kwargs = engine._scan_symbol.call_args
        assert call_args[0] == "SBIN"
        assert call_kwargs.get("open_positions") == []  # open_positions passed through cleanly


class TestStrategyAwareTTL:
    @pytest.mark.asyncio
    async def test_validate_pending_opportunities_per_strategy_ttl_orb_vs_mrf(self):
        """ORB expires at 180s while MRF remains alive at the same age."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")

        engine = _make_engine()
        engine.state = EngineState.RUNNING
        now = datetime.now(IST)

        # Real config with tiered TTL from defaults.yaml
        from config.settings import Settings
        engine.config = Settings()

        # Market open mock
        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True

        # Both opportunities created 200 seconds ago
        created_at_200s_ago = (now - timedelta(seconds=200)).isoformat()

        engine.pending_opportunities["opp-orb-1"] = {
            "id": "opp-orb-1",
            "symbol": "RELIANCE",
            "strategy": "ORB",
            "direction": "BUY",
            "created_at": created_at_200s_ago,
            "entry_price": 2500.0,
            "stop_loss": 2450.0,
            "target": 2600.0,
        }
        engine.pending_opportunities["opp-mrf-1"] = {
            "id": "opp-mrf-1",
            "symbol": "INFY",
            "strategy": "MRF",
            "direction": "BUY",
            "created_at": created_at_200s_ago,
            "entry_price": 1500.0,
            "stop_loss": 1470.0,
            "target": 1560.0,
        }

        engine._repo_context = MagicMock()
        engine._repo_context.return_value.__aenter__.return_value = None
        engine._broadcast = AsyncMock()

        await engine._validate_pending_opportunities()

        # ORB (180s TTL) must be invalidated at 200s
        assert "opp-orb-1" not in engine.pending_opportunities
        assert "opp-orb-1" in engine.invalidated_opportunities
        assert engine.invalidated_opportunities["opp-orb-1"]["invalidation_code"] == "SETUP_TIMEOUT_EXPIRED"

        # MRF (360s TTL) must still be pending at 200s
        assert "opp-mrf-1" in engine.pending_opportunities
        assert "opp-mrf-1" not in engine.invalidated_opportunities

    @pytest.mark.asyncio
    async def test_validate_pending_opportunities_fallback_ttl(self):
        """Unmapped strategy uses default fallback opportunity_ttl_seconds."""
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        IST = ZoneInfo("Asia/Kolkata")

        engine = _make_engine()
        engine.state = EngineState.RUNNING
        now = datetime.now(IST)

        engine.config = MagicMock()
        engine.config.get_risk_config.return_value = {
            "opportunity_ttl_seconds": 300,
            "strategy_ttl_seconds": {"ORB": 180},
            "price_mismatch_threshold_pct": 0.6,
        }

        engine.market_hours = MagicMock()
        engine.market_hours.is_market_open.return_value = True

        # Unmapped strategy created 250s ago (within 300s fallback)
        engine.pending_opportunities["opp-custom-1"] = {
            "id": "opp-custom-1",
            "symbol": "TCS",
            "strategy": "CUSTOM_TREND",
            "direction": "BUY",
            "created_at": (now - timedelta(seconds=250)).isoformat(),
            "entry_price": 3800.0,
            "stop_loss": 3750.0,
            "target": 3900.0,
        }

        engine._repo_context = MagicMock()
        engine._repo_context.return_value.__aenter__.return_value = None
        engine._broadcast = AsyncMock()

        await engine._validate_pending_opportunities()

        # Still pending at 250s
        assert "opp-custom-1" in engine.pending_opportunities

        # Age it past 300s (e.g. 320s)
        engine.pending_opportunities["opp-custom-1"]["created_at"] = (now - timedelta(seconds=320)).isoformat()
        await engine._validate_pending_opportunities()

        # Now expired under 300s fallback TTL
        assert "opp-custom-1" not in engine.pending_opportunities
        assert "opp-custom-1" in engine.invalidated_opportunities
        assert engine.invalidated_opportunities["opp-custom-1"]["invalidation_code"] == "SETUP_TIMEOUT_EXPIRED"
