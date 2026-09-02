"""
Permanent regression tests for the G16 trend-wiring fix (audit claim #3).

Before v0.4.3, NO production code ever populated context["trend"] /
context["nifty_trend"] — G16MultiTimeframe ran in permanent "neutral" mode,
so its counter-trend protection (BUY-in-bear / SELL-in-bull blocks) could
NEVER fire in live trading (tests passed only because fixtures supplied the
key explicitly). These tests pin the full wiring chain:

  engine.current_regime
    → engine._regime_to_trend()
    → _build_risk_context()["trend"] / ["nifty_trend"]
    → G16 resolution (trend → nifty_trend → regime → neutral, explicit
      None checks — an empty string is KEPT and treated as neutral, never
      silently falling through to the next key)
    → gate behaviour (counter-trend blocks, strict neutral branch)
    → end-to-end RiskEngine.validate() blocking a BUY in a Bear regime
      using the engine's REAL context.
"""
from datetime import datetime, time
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from risk.gates.g16_multi_timeframe import G16MultiTimeframe
from risk.risk_engine import RiskEngine

IST = ZoneInfo("Asia/Kolkata")


def _make_engine():
    from core.engine import UltraBotEngine

    config = MagicMock()
    config.get_risk_config.return_value = {}
    config.get_partial_booking_config.return_value = {}
    engine = UltraBotEngine(
        config=config,
        repository_getter=MagicMock(),
        error_engine=MagicMock(),
        risk_engine=MagicMock(),
        position_sizer=MagicMock(),
        partial_booker=None,
        daily_risk_manager=None,  # → daily_status None → clean defaults
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )
    return engine


# ---------------------------------------------------------------------------
# 1. Regime → trend mapping
# ---------------------------------------------------------------------------
class TestRegimeToTrendMapping:
    """_regime_to_trend() maps every live regime value onto G16's vocabulary."""

    @pytest.mark.parametrize(
        "regime,expected",
        [
            ("Bull", "bull"),
            ("Bear", "bear"),
            ("Sideways", "neutral"),
            ("Volatile", "neutral"),  # volatility is a state, not a direction
            ("bull", "bull"),
            ("BEAR", "bear"),
            (" bull ", "bull"),
            ("bullish", "bull"),
            ("bearish", "bear"),
            ("up", "bull"),
            ("down", "bear"),
            ("", "neutral"),
            ("confused", "neutral"),  # unknown → strictest branch, never silent pass
        ],
    )
    def test_mapping(self, regime, expected):
        engine = _make_engine()
        engine.current_regime = regime
        assert engine._regime_to_trend() == expected

    def test_none_regime_maps_to_neutral(self):
        engine = _make_engine()
        engine.current_regime = None
        assert engine._regime_to_trend() == "neutral"


# ---------------------------------------------------------------------------
# 2. Engine risk-context wiring (the previously-dead supply side)
# ---------------------------------------------------------------------------
class TestEngineContextWiring:
    """_build_risk_context() must supply trend/nifty_trend on EVERY call."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "regime,expected_trend",
        [
            ("Bull", "bull"),
            ("Bear", "bear"),
            ("Sideways", "neutral"),
            ("Volatile", "neutral"),
        ],
    )
    async def test_context_carries_trend_for_every_regime(self, regime, expected_trend):
        engine = _make_engine()
        engine.current_regime = regime
        ctx = await engine._build_risk_context(
            {"strategy": "ORB", "direction": "BUY"}, "RELIANCE", 2500.0, open_positions=[]
        )
        assert ctx["trend"] == expected_trend, (
            f"regime {regime!r} must surface as trend {expected_trend!r} in the risk context"
        )
        assert ctx["nifty_trend"] == expected_trend, "alias key must stay in sync"

    @pytest.mark.asyncio
    async def test_context_trend_is_always_a_nonempty_string(self):
        """Wiring contract core: the key can be missing never, empty never."""
        engine = _make_engine()
        engine.current_regime = ""  # worst case: engine never classified
        ctx = await engine._build_risk_context({}, "RELIANCE", 2500.0, open_positions=[])
        assert isinstance(ctx["trend"], str) and ctx["trend"] in ("bull", "bear", "neutral")

    @pytest.mark.asyncio
    async def test_context_keeps_regime_key(self):
        """The raw regime value remains available (used by other consumers)."""
        engine = _make_engine()
        engine.current_regime = "Bear"
        ctx = await engine._build_risk_context({}, "RELIANCE", 2500.0, open_positions=[])
        assert ctx["regime"] == "Bear"


# ---------------------------------------------------------------------------
# 3. G16 resolution semantics (explicit None checks — no falsy fall-through)
# ---------------------------------------------------------------------------
class TestG16Resolution:
    def _gate(self):
        return G16MultiTimeframe({"require_trend_alignment": True})

    @pytest.mark.asyncio
    async def test_empty_string_trend_does_not_pull_second_key(self):
        """Audit claim #3 core: '' is a VALID value ('no clear trend'), it must
        NOT silently fall through to nifty_trend (old falsy-or behavior)."""
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "Momentum", "confidence": 0.9},
            {"trend": "", "nifty_trend": "bear"},
        )
        # "" → neutral (not bear): a non-breakout strategy passes in neutral,
        # but the bear block must NOT be triggered by the aliased key.
        assert res.passed is True
        assert "neutral" in res.message

    @pytest.mark.asyncio
    async def test_empty_string_trend_applies_strict_neutral_branch(self):
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "ORB_Breakout", "confidence": 0.5},
            {"trend": ""},
        )
        assert res.passed is False, "empty trend ⇒ neutral ⇒ breakout needs conf ≥ 0.60"

    @pytest.mark.asyncio
    async def test_missing_trend_uses_nifty_trend(self):
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "Momentum", "confidence": 0.9},
            {"nifty_trend": "bear"},
        )
        assert res.passed is False
        assert "Bearish" in res.message or "Bear" in res.message

    @pytest.mark.asyncio
    async def test_missing_trend_and_nifty_trend_falls_back_to_regime(self):
        """Defense in depth: even if the engine forgot 'trend', the context's
        regime value still activates the counter-trend block."""
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "Momentum", "confidence": 0.9},
            {"regime": "Bear"},
        )
        assert res.passed is False
        assert "counter-trend" in res.message

    @pytest.mark.asyncio
    async def test_volatile_trend_is_treated_as_neutral_not_silent_pass(self):
        """'volatile' is not a direction: must hit the strict neutral branch —
        NOT fall through every branch and pass unconditionally (dead-gate mode)."""
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "TrendFollow", "confidence": 0.5},
            {"trend": "volatile"},
        )
        assert res.passed is False, "volatile ⇒ neutral ⇒ breakout needs conf ≥ 0.60"

    @pytest.mark.asyncio
    async def test_unknown_trend_value_is_treated_as_neutral(self):
        res = await self._gate().check(
            {"direction": "BUY", "strategy": "Breakout", "confidence": 0.5},
            {"trend": "beear"},  # typo — must not silently pass every branch
        )
        assert res.passed is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "direction,trend",
        [
            ("BUY", "bear"),
            ("LONG", "bearish"),
            ("SELL", "bull"),
            ("SHORT", "bullish"),
        ],
    )
    async def test_counter_trend_blocks(self, direction, trend):
        """The previously-dead protection: counter-trend signals are blocked."""
        res = await self._gate().check(
            {"direction": direction, "strategy": "Momentum", "confidence": 0.9},
            {"trend": trend},
        )
        assert res.passed is False
        assert "counter-trend" in res.message

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "direction,trend",
        [("BUY", "bull"), ("SELL", "bear")],
    )
    async def test_aligned_trend_passes(self, direction, trend):
        res = await self._gate().check(
            {"direction": direction, "strategy": "Momentum", "confidence": 0.9},
            {"trend": trend},
        )
        assert res.passed is True

    @pytest.mark.asyncio
    async def test_neutral_breakout_confidence_threshold_preserved(self):
        gate = self._gate()
        low = await gate.check(
            {"direction": "BUY", "strategy": "Breakout", "confidence": 0.5}, {"trend": "neutral"}
        )
        high = await gate.check(
            {"direction": "BUY", "strategy": "Breakout", "confidence": 0.7}, {"trend": "neutral"}
        )
        assert low.passed is False and high.passed is True

    @pytest.mark.asyncio
    async def test_require_alignment_false_disables_blocks(self):
        gate = G16MultiTimeframe({"require_trend_alignment": False})
        res = await gate.check(
            {"direction": "BUY", "strategy": "Momentum", "confidence": 0.9},
            {"trend": "bear"},
        )
        assert res.passed is True


# ---------------------------------------------------------------------------
# 4. End-to-end: the REAL engine context + REAL RiskEngine → G16 fires
# ---------------------------------------------------------------------------
class TestEndToEndChain:
    @pytest.mark.asyncio
    async def test_risk_engine_blocks_buy_in_bear_regime_with_real_context(self):
        """The money test: a BUY signal against the engine's own Bear-regime
        context must be blocked BY G16 through the full 18-gate pipeline."""
        engine = _make_engine()
        engine.current_regime = "Bear"

        signal = {
            "symbol": "RELIANCE",
            "strategy": "ORB",
            "direction": "BUY",
            "confidence": 0.9,
            "entry_price": 2500.0,
            "quantity": 1,
        }
        ctx = await engine._build_risk_context(signal, "RELIANCE", 2500.0, open_positions=[])
        # Simulate a mid-session evaluation so G8's time window passes on a
        # weekend test run (G8 checks wall-clock time only, no weekday).
        ctx["current_time"] = datetime.combine(datetime.now(IST).date(), time(11, 0), tzinfo=IST)

        risk_engine = RiskEngine({"max_open_positions": 3, "max_daily_trades": 10})
        result = await risk_engine.evaluate(signal=signal, symbol="RELIANCE", context=ctx)

        assert result.passed is False, (
            f"BUY in Bear regime must be blocked; got passed=True "
            f"(blocked_by={result.blocked_by})"
        )
        assert result.blocked_by == "G16_MultiTimeframe", (
            f"expected the G16 counter-trend block, got blocked_by={result.blocked_by!r} "
            f"({result.block_reason})"
        )
        assert "counter-trend" in (result.block_reason or "")

    @pytest.mark.asyncio
    async def test_risk_engine_allows_buy_in_bull_regime_with_real_context(self):
        """Same chain, aligned direction → passes all 18 gates including G16."""
        engine = _make_engine()
        engine.current_regime = "Bull"

        signal = {
            "symbol": "RELIANCE",
            "strategy": "ORB",
            "direction": "BUY",
            "confidence": 0.9,
            "entry_price": 2500.0,
            "quantity": 1,
        }
        ctx = await engine._build_risk_context(signal, "RELIANCE", 2500.0, open_positions=[])
        ctx["current_time"] = datetime.combine(datetime.now(IST).date(), time(11, 0), tzinfo=IST)

        risk_engine = RiskEngine({"max_open_positions": 3, "max_daily_trades": 10})
        result = await risk_engine.evaluate(signal=signal, symbol="RELIANCE", context=ctx)

        assert result.passed is True, (
            f"aligned BUY in Bull regime should pass; blocked_by={result.blocked_by} "
            f"({result.block_reason})"
        )
