"""
Permanent regression test for Branch 3:
1. Sideways regime classification completion (RegimeDetector / AdaptiveManager wiring + fallback)
2. Opposing-signal handling & position conflict validation
3. Scan telemetry distinction between NO_SETUP and ERROR (exceptions)
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from core.engine import UltraBotEngine
from strategies.regime_detector import RegimeDetector


def _make_engine(regime_detector=None, adaptive_manager=None):
    config = MagicMock()
    config.get_risk_config.return_value = {}
    config.get_regime_config.return_value = {"high_vix_threshold": 22.0}
    config.get_partial_booking_config.return_value = {}
    config.get_strategy_activation.side_effect = lambda r: {
        "Bull": {"active": ["ORB", "MB", "PTC"]},
        "Bear": {"active": ["ORB", "TRS"]},
        "Sideways": {"active": ["ORB", "MRF", "VC", "SIC"]},
        "Volatile": {"active": ["ORB", "MRF"]},
    }.get(r, {"active": []})

    engine = UltraBotEngine(
        config=config,
        repository_getter=MagicMock(),
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=None,
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
        regime_detector=regime_detector,
        adaptive_manager=adaptive_manager,
    )
    return engine


# ---------------------------------------------------------------------------
# 1. Regime Classification Tests
# ---------------------------------------------------------------------------

class TestRegimeClassification:

    def test_regime_detector_wires_and_classifies_sideways(self):
        detector = RegimeDetector()
        engine = _make_engine(regime_detector=detector)
        engine.nifty_price = 22000.0
        engine.nifty_change = 0.05
        engine.vix = 13.5
        engine.current_regime = "Bull"

        engine._update_regime_simple()

        assert engine.current_regime == "Sideways"
        assert set(engine.active_strategies) == {"ORB", "MRF", "VC", "SIC"}

    def test_regime_fallback_classifies_sideways_when_no_detector(self):
        engine = _make_engine(regime_detector=None)
        engine.nifty_change = 0.1
        engine.vix = 14.0
        engine.current_regime = "Bull"

        engine._update_regime_simple()

        assert engine.current_regime == "Sideways"
        assert set(engine.active_strategies) == {"ORB", "MRF", "VC", "SIC"}

    def test_regime_fallback_classifies_bull(self):
        engine = _make_engine(regime_detector=None)
        engine.nifty_change = 0.8
        engine.vix = 14.0
        engine.current_regime = "Sideways"

        engine._update_regime_simple()

        assert engine.current_regime == "Bull"
        assert set(engine.active_strategies) == {"ORB", "MB", "PTC"}

    def test_regime_fallback_classifies_bear(self):
        engine = _make_engine(regime_detector=None)
        engine.nifty_change = -0.6
        engine.vix = 19.0
        engine.current_regime = "Sideways"

        engine._update_regime_simple()

        assert engine.current_regime == "Bear"
        assert set(engine.active_strategies) == {"ORB", "TRS"}

    def test_regime_fallback_classifies_volatile(self):
        engine = _make_engine(regime_detector=None)
        engine.vix = 25.0
        engine.current_regime = "Sideways"

        engine._update_regime_simple()

        assert engine.current_regime == "Volatile"
        assert set(engine.active_strategies) == {"ORB", "MRF"}


# ---------------------------------------------------------------------------
# 2. Opposing Signal & Position Conflict Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestOpposingSignalsAndPositionConflicts:

    async def test_opposing_pending_opportunity_superseded_by_higher_conviction(self):
        engine = _make_engine()
        engine.active_strategies = ["ORB"]
        engine._broadcast = AsyncMock()
        engine._run_risk_gates = AsyncMock(return_value={"passed": True, "all_gates": []})
        engine._calculate_position_size = AsyncMock(return_value={"quantity": 1})

        # Pre-seed a weaker pending BUY opportunity
        engine.pending_opportunities["opp-old-123"] = {
            "id": "opp-old-123",
            "symbol": "RELIANCE",
            "direction": "BUY",
            "strategy": "MRF",
            "confidence": 0.65,
            "entry_price": 2500.0,
            "stop_loss": 2480.0,
            "target": 2540.0,
        }

        # Mock feed returning valid candles
        candles = [{"close": 2500.0, "high": 2510.0, "low": 2490.0, "open": 2495.0, "volume": 1000} for _ in range(30)]
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(return_value=candles)

        # Higher conviction SELL signal
        strong_sell_signal = {
            "symbol": "RELIANCE", "direction": "SELL",
            "entry_price": 2500.0, "sl_price": 2520.0, "target_price": 2460.0,
            "confidence": 0.85, "strategy": "ORB"
        }
        engine._execute_strategy_scan = AsyncMock(return_value=strong_sell_signal)

        repo = MagicMock()
        repo.get_open_positions = AsyncMock(return_value=[])
        repo.create_signal = AsyncMock(return_value=MagicMock(id="sig-new-456"))

        await engine._scan_symbol("RELIANCE", repo)

        # Old opportunity should be removed from pending and added to invalidated
        assert "opp-old-123" not in engine.pending_opportunities
        assert "opp-old-123" in engine.invalidated_opportunities
        inv = engine.invalidated_opportunities["opp-old-123"]
        assert inv["invalidation_code"] == "OPPOSING_SIGNAL_SUPERSEDED"

        # New opportunity should be pending
        assert len(engine.pending_opportunities) == 1
        new_opp = list(engine.pending_opportunities.values())[0]
        assert new_opp["direction"] == "SELL"

    async def test_opposing_pending_opportunity_rejects_lower_conviction(self):
        engine = _make_engine()
        engine.active_strategies = ["ORB"]
        engine._run_risk_gates = AsyncMock(return_value={"passed": True, "all_gates": []})

        # Pre-seed a stronger pending BUY opportunity
        engine.pending_opportunities["opp-strong-123"] = {
            "id": "opp-strong-123",
            "symbol": "RELIANCE",
            "direction": "BUY",
            "strategy": "MRF",
            "confidence": 0.85,
            "entry_price": 2500.0,
            "stop_loss": 2480.0,
            "target": 2540.0,
        }

        # Mock feed returning valid candles
        candles = [{"close": 2500.0, "high": 2510.0, "low": 2490.0, "open": 2495.0, "volume": 1000} for _ in range(30)]
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(return_value=candles)

        # Weaker SELL signal
        weak_sell_signal = {
            "symbol": "RELIANCE", "direction": "SELL",
            "entry_price": 2500.0, "sl_price": 2520.0, "target_price": 2460.0,
            "confidence": 0.70, "strategy": "ORB"
        }
        engine._execute_strategy_scan = AsyncMock(return_value=weak_sell_signal)

        repo = MagicMock()
        repo.get_open_positions = AsyncMock(return_value=[])

        await engine._scan_symbol("RELIANCE", repo)

        assert engine._signals_rejected_count == 1
        assert engine._rejections_by_gate.get("OPPOSING_SIGNAL_CONFLICT", 0) == 1
        # Original strong opportunity remains pending
        assert "opp-strong-123" in engine.pending_opportunities


# ---------------------------------------------------------------------------
# 3. Telemetry Distinction Tests (NO_SETUP vs ERROR)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestScanTelemetryDistinction:

    async def test_no_setup_emits_no_setup_telemetry(self):
        engine = _make_engine()
        engine.active_strategies = ["ORB"]

        candles = [{"close": 2500.0, "high": 2510.0, "low": 2490.0, "open": 2495.0, "volume": 1000} for _ in range(30)]
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(return_value=candles)

        # Strategy returned None (no setup triggered)
        engine._execute_strategy_scan = AsyncMock(return_value=None)
        repo = MagicMock()

        await engine._scan_symbol("RELIANCE", repo)

        assert len(engine._recent_scan_telemetry) == 1
        event = engine._recent_scan_telemetry[0]
        assert event["status"] == "NO_SETUP"
        assert event["reason"] == "Strategy entry criteria not met"
        assert engine._errors_count == 0

    async def test_exception_emits_error_telemetry_and_increments_errors_count(self):
        engine = _make_engine()
        engine.active_strategies = ["ORB"]

        candles = [{"close": 2500.0, "high": 2510.0, "low": 2490.0, "open": 2495.0, "volume": 1000} for _ in range(30)]
        engine.feed = MagicMock()
        engine.feed.get_candles = AsyncMock(return_value=candles)

        # Strategy raised unhandled exception (e.g. calculation crash)
        engine._execute_strategy_scan = AsyncMock(side_effect=ZeroDivisionError("division by zero in indicator"))
        repo = MagicMock()

        await engine._scan_symbol("RELIANCE", repo)

        assert len(engine._recent_scan_telemetry) == 1
        event = engine._recent_scan_telemetry[0]
        assert event["status"] == "ERROR"
        assert event["gate"] == "STRATEGY_EXCEPTION"
        assert "division by zero" in event["reason"]
        assert engine._errors_count == 1
