"""
Permanent regression test for the confidence=0.0 falsy bug.

Guards against:
1. G10 (MinConfidence) treating confidence=0.0 as falsy and bumping it to a default.
2. G16 (MultiTimeframe) treating confidence=0.0 as falsy and bumping it to 0.60 in neutral trends.
3. G14 (StrategyBacktest) treating confidence=0.0 as falsy and bumping win_rate to 0.50.
4. engine._build_opportunity treating confidence=0.0 as falsy and bumping raw_conf to 0.60.

This ensures a signal that explicitly sets confidence=0.0 is accurately evaluated as 0.0.
"""
import pytest
from unittest.mock import MagicMock

from risk.gates.g10_min_confidence import G10MinConfidence
from risk.gates.g16_multi_timeframe import G16MultiTimeframe
from risk.gates.g14_strategy_backtest import G14StrategyBacktest


def _make_engine():
    from core.engine import UltraBotEngine
    config = MagicMock()
    config.get_risk_config.return_value = {}
    config.get_partial_booking_config.return_value = {}
    return UltraBotEngine(
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
    )


@pytest.mark.asyncio
class TestConfidenceFalsyBug:

    async def test_g10_rejects_explicit_zero_confidence(self):
        """G10 should reject confidence=0.0 against a min_confidence of 0.60."""
        gate = G10MinConfidence({"min_signal_confidence": 0.6})
        
        # Explicit 0.0
        signal = {"direction": "BUY", "confidence": 0.0}
        res = await gate.check(signal, {})
        
        assert not res.passed
        assert res.value == 0.0, "confidence=0.0 should be read as 0.0, not a default"
        assert "below minimum" in res.message

    async def test_g10_handles_missing_confidence(self):
        """G10 should treat completely missing confidence as 0.0."""
        gate = G10MinConfidence({"min_signal_confidence": 0.6})
        
        # Missing key
        signal = {"direction": "BUY"}
        res = await gate.check(signal, {})
        
        assert not res.passed
        assert res.value == 0.0

    async def test_g16_rejects_explicit_zero_confidence_in_neutral_trend(self):
        """
        In neutral trends, breakout strategies require >= 0.60 confidence.
        If a signal explicitly sets confidence=0.0, it MUST fail and not be bumped to 0.60.
        """
        gate = G16MultiTimeframe({"require_trend_alignment": True})
        
        signal = {
            "direction": "BUY",
            "strategy": "ORB_Breakout",
            "confidence": 0.0
        }
        context = {"trend": "neutral"}
        
        res = await gate.check(signal, context)
        
        assert not res.passed
        assert res.value == 0.0, "confidence=0.0 should be read as 0.0, not fallback to 0.60"
        assert "requires higher conviction" in res.message

    async def test_g16_handles_missing_confidence_in_neutral_trend(self):
        """Missing confidence defaults to 0.0 and fails the neutral breakout check."""
        gate = G16MultiTimeframe({"require_trend_alignment": True})
        
        signal = {
            "direction": "BUY",
            "strategy": "ORB_Breakout"
        }
        context = {"trend": "neutral"}
        
        res = await gate.check(signal, context)
        
        assert not res.passed
        assert res.value == 0.0

    async def test_g14_no_stats_passes_honestly_without_fabrication(self):
        """
        With no backtest data and no live stats, G14 must pass with an honest
        'insufficient history' note — it must NOT invent a win rate from
        confidence (confidence is not a historical win rate).
        """
        gate = G14StrategyBacktest({"min_strategy_win_rate": 0.40})

        signal = {
            "strategy": "UnknownStrategy",
            "symbol": "RELIANCE",
            "confidence": 0.0  # Explicit zero
        }
        # No backtest data in context, no strategy_stats
        res = await gate.check(signal, {})

        assert res.passed is True
        assert res.value is None, "No fabricated win rate should be derived from confidence"
        assert "insufficient history" in res.message

    async def test_g14_real_stats_with_zero_win_rate_blocks(self):
        """Real stats with win_rate=0.0 must be honoured as 0.0 (not bumped)."""
        gate = G14StrategyBacktest({"min_backtest_win_rate": 0.40})

        signal = {
            "strategy": "UnknownStrategy",
            "symbol": "RELIANCE",
            "confidence": 0.0,
        }
        context = {
            "strategy_stats": {
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_trades": 25,
                "source": "db_strategy_performance",
            }
        }
        res = await gate.check(signal, context)

        assert not res.passed
        assert res.value == 0.0, "Win rate from real stats must be exactly 0.0, never bumped"

    async def test_engine_build_opportunity_conviction_score_with_zero_confidence(self):
        """engine._build_opportunity should calculate conviction based on raw_conf=0.0."""
        engine = _make_engine()
        
        signal = {
            "direction": "BUY",
            "entry_price": 100,
            "sl_price": 90,
            "target_price": 120,
            "confidence": 0.0  # Explicit zero
        }
        
        opp = engine._build_opportunity(
            signal=signal,
            strategy_name="ORB",
            symbol="TEST",
            current_price=100,
            sizing={"quantity": 1},
            risk_result={"all_gates": [{"passed": True}], "passed": True},
        )
        
        # raw_conf = 0.0. composite_score = max(0, 0.0*0.7 + 1.0*0.2 + 0.05 (rr >= 2.0 bonus)) = 0.25 -> 25.0
        # If bug existed (raw_conf=0.6), score would be 0.6*0.7 + 0.2 + 0.05 = 0.67 -> 67.0
        assert opp["conviction_breakdown"]["technical_confidence"] == 0.0, "raw_conf should be 0.0, not 0.60"
        assert opp["composite_score"] == pytest.approx(25.0), "composite_score should reflect 0.0 technical confidence (25.0 vs 67.0 with bug)"
