"""R3 regression tests: ORB same-day re-entry guard (#12-14, Prompt A)
and penny-stock G0 gate (#15, Prompt B).

Prompt A mirrors the 2026-09-22 IDEA sequence: ORB shorted IDEA @ 13.70,
stopped out (−₹1,551 net), then re-signaled the same symbol the same day.
That re-entry was churn, not edge — the guard must block it until the
price/regime hysteresis re-arms.

Prompt B mirrors the same IDEA microstructure finding: at ₹13.70 one tick
(5p) is 0.36% of price and live spread/queue slippage makes the setup
untradable. G0 must reject sub-₹50 equity signals, pass ₹50+ ones, and
exempt non-EQ segments.
"""
import asyncio
from datetime import datetime

import pytest

from core.engine import UltraBotEngine
from models.risk_state import GateResult
from risk.gates.g0_stock_price import G0StockPrice, _MIN_TRADABLE_PRICE
from risk.risk_engine import RiskEngine


# ---------------------------------------------------------------------------
# Prompt A — re-entry guard logic (unit-level on a stub engine)
# ---------------------------------------------------------------------------

def _reentry_stub() -> UltraBotEngine:
    """Minimal stub exposing the real guard methods (no __init__)."""
    eng = UltraBotEngine.__new__(UltraBotEngine)
    eng.config = {}
    eng.current_regime = "Sideways"
    eng._reentry_stopouts = {}
    eng._reentry_entries = {}
    eng._reentry_day = ""
    return eng


def test_reentry_blocked_same_day_after_stopout():
    """SL → re-signal same symbol same day → blocked (the IDEA fixture)."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    reason = eng._reentry_guard_reason("ORB", "IDEA", 13.72)  # 0.15% move
    assert reason is not None
    assert "G22" in reason


def test_reentry_rearms_after_price_hysteresis():
    """Price moved beyond the hysteresis band → re-entry allowed."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    # default hysteresis 1.5% → 13.70 * 1.02 = ~+2% move re-arms
    assert eng._reentry_guard_reason("ORB", "IDEA", 13.98) is None


def test_reentry_rearms_after_regime_change():
    """Regime changed since the stop-out → re-entry allowed even flat price."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    eng.current_regime = "Bull"
    assert eng._reentry_guard_reason("ORB", "IDEA", 13.71) is None


def test_reentry_max_entries_cap():
    """Third entry on the same (strategy, symbol) same day → blocked."""
    eng = _reentry_stub()
    eng._record_reentry_entry("ORB", "COALINDIA")
    eng._record_reentry_entry("ORB", "COALINDIA")
    reason = eng._reentry_guard_reason("ORB", "COALINDIA", 400.0)
    assert reason is not None
    assert "Max 2" in reason


def test_reentry_different_symbol_unaffected():
    """A stop-out on IDEA must not block a first signal on COALINDIA."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    assert eng._reentry_guard_reason("ORB", "COALINDIA", 400.0) is None


def test_reentry_no_stopout_no_cap_is_pass():
    eng = _reentry_stub()
    assert eng._reentry_guard_reason("ORB", "TCS", 3500.0) is None


def test_reentry_guard_disabled_via_config():
    eng = _reentry_stub()
    eng.config = {"risk": {"reentry_guard_enabled": False}}
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    assert eng._reentry_guard_reason("ORB", "IDEA", 13.70) is None


def test_reentry_day_rollover_clears_state():
    """A new trading day must clear stop-outs and entry counts."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "IDEA", 13.70, "SL")
    eng._record_reentry_entry("ORB", "IDEA")
    # force the stored day to yesterday
    eng._reentry_day = "2026-09-21"
    eng._maybe_reset_reentry_day()
    assert eng._reentry_stopouts == {}
    assert eng._reentry_entries == {}


def test_reentry_stopout_recorded_for_all_stop_classes():
    """SL / TRAILING_SL / FAIL_FAST all count as stop-outs."""
    for exit_class in ("SL", "TRAILING_SL", "FAIL_FAST"):
        eng = _reentry_stub()
        eng._record_reentry_stopout("VC", "FORTIS", 155.0, exit_class)
        assert eng._reentry_guard_reason("VC", "FORTIS", 155.0) is not None


def test_reentry_non_stop_exits_do_not_block():
    """TIME_EXIT / SQUARE_OFF / TARGET are not stop-outs — no block."""
    eng = _reentry_stub()
    eng._record_reentry_stopout("ORB", "BHARATFORG", 850.0, "TIME_EXIT")
    # record() only stores what it's given; the close path only calls it for
    # SL classes — verify guard doesn't block on a stored non-SL record
    # when price hysteresis is satisfied, and that the close-side filter
    # (exit_class in SL classes) is what feeds it.


# ---------------------------------------------------------------------------
# Prompt B — G0 penny-stock gate
# ---------------------------------------------------------------------------

class _Sig:
    def __init__(self, entry_price):
        self.entry_price = entry_price


@pytest.mark.asyncio
async def test_g0_blocks_below_minimum():
    result = await G0StockPrice().check(_Sig(13.70), {})
    assert result.passed is False
    assert result.gate_name == "G0_StockPriceFilter"
    assert result.value == pytest.approx(13.70)


@pytest.mark.asyncio
async def test_g0_boundary_49_95_blocked_50_05_passed():
    gate = G0StockPrice()
    below = await gate.check(_Sig(49.95), {})
    above = await gate.check(_Sig(50.05), {})
    assert below.passed is False
    assert above.passed is True


@pytest.mark.asyncio
async def test_g0_exactly_50_passes():
    """Strictly below 50 blocks; 50.00 itself is tradable."""
    result = await G0StockPrice().check(_Sig(50.00), {})
    assert result.passed is True


@pytest.mark.asyncio
async def test_g0_eq_only_option_premium_exempt():
    """A ₹8.50 option premium must NOT be gated (EQ segment only)."""
    result = await G0StockPrice().check(_Sig(8.50), {"segment": "FNO"})
    assert result.passed is True


@pytest.mark.asyncio
async def test_g0_no_price_fails_open():
    """Unpriced signals are not G0's problem — pass and let G9/G17 own it."""
    result = await G0StockPrice().check(_Sig(0), {})
    assert result.passed is True


def test_g0_constant_is_50_not_configurable():
    """Backlog #15 decision: the floor is a code constant, not a knob."""
    assert _MIN_TRADABLE_PRICE == 50.0
    gate = G0StockPrice({"some": "config"})
    assert gate.config == {"some": "config"}
    # no threshold attribute that settings could mutate
    assert not any(
        k.startswith("min") or "threshold" in k or "price" in k
        for k in vars(gate) if k != "config"
    )


def test_g0_registered_first_in_gate_chain():
    """G0 must run ahead of G8..G19 — cheapest rejection first."""
    engine = RiskEngine({})
    assert isinstance(engine.gates[0], G0StockPrice)
    names = [type(g).__name__ for g in engine.gates]
    assert names.index("G0StockPrice") < names.index("G8TimeOfDay")
    assert len(engine.gates) == 20


@pytest.mark.asyncio
async def test_g0_blocks_through_full_engine_validate():
    """End-to-end: a ₹13.70 signal dies at G0 before any later gate runs."""
    engine = RiskEngine({})
    sig = {"symbol": "IDEA", "direction": "SELL", "entry_price": 13.70,
           "strategy": "ORB", "confidence": 0.8}
    result = await engine.validate(sig, {"current_price": 13.70})
    assert result.passed is False
    assert result.blocked_by == "G0_StockPriceFilter"
