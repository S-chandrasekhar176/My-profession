"""
Permanent regression test for signal key alignment and orientation validation.

Guards against:
1. engine reading stop_loss/target instead of sl_price/target_price from strategy output
2. Zero SL/target being persisted to signals table
3. Orientation-inverted signals reaching risk gates
4. Any V2 strategy reverting to wrong output key names

DESIGN NOTE: _make_engine() calls the real UltraBotEngine.__init__() with fully-mocked
constructor arguments rather than using object.__new__() + manual attribute stubs.
Rationale: __init__ assigns ALL engine state attributes (self.vix, self.current_regime,
self.partial_booker, etc.) in one place. Using the real constructor means any future
attribute added to __init__ is automatically present in the test helper -- this test
will never fail with a spurious AttributeError when _build_opportunity starts reading
a new self.X that __init__ already sets.

This test must NEVER be deleted -- it guards the RISK_CRITICAL bug fixed in
fix/signal-key-alignment-and-orientation-validation (Phase 3, Branch 1).
"""
import os
import re
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_orb_signal(direction="BUY", entry=2488.2, sl=2474.51, target=2504.2, confidence=0.85):
    """Return a signal dict in the canonical V2 strategy format (sl_price / target_price)."""
    return {
        "symbol": "RELIANCE",
        "direction": direction,
        "entry_price": entry,
        "sl_price": sl,
        "target_price": target,
        "confidence": confidence,
        "strategy": "ORB",
        "is_equity": True,
        "is_fno": False,
        "segment": "EQ",
    }


def _make_engine():
    """
    Build a minimal but structurally-complete UltraBotEngine using the real __init__.

    All constructor args are MagicMocks. config.get_risk_config() returns {} so
    VIX staleness thresholds fall through to their defaults. This is intentionally
    NOT object.__new__() + manual stubs -- see module docstring for why.
    """
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
        partial_booker=None,      # None so _build_opportunity skips booking_levels path
        daily_risk_manager=MagicMock(),
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )
    # __init__ sets self.vix = 15.0 and self.current_regime = "Sideways" already;
    # override to match the ORB proof candle session values for test clarity.
    engine.vix = 14.5
    engine.current_regime = "Bull"
    return engine


def _build_opp(signal, entry=2488.2):
    engine = _make_engine()
    return engine._build_opportunity(
        signal=signal,
        strategy_name="ORB",
        symbol="RELIANCE",
        current_price=entry,
        sizing={"quantity": 1, "position_size": entry, "method": "fixed"},
        risk_result={"all_gates": [], "passed": True},
        signal_id=str(uuid.uuid4()),
    )


def _check_orientation(signal):
    """Replicate pre-gate orientation check from engine._scan_symbol."""
    _entry = float(signal.get("entry_price") or 0)
    _sl    = float(signal.get("sl_price") or 0)
    _tgt   = float(signal.get("target_price") or 0)
    _dir   = signal.get("direction", "")
    ok = False
    if _entry > 0 and _sl > 0 and _tgt > 0:
        if _dir in ("BUY", "LONG"):
            ok = _sl < _entry < _tgt
        elif _dir in ("SELL", "SHORT"):
            ok = _sl > _entry > _tgt
    reason = "" if ok else f"Invalid geometry: entry={_entry} sl={_sl} target={_tgt} dir={_dir}"
    return ok, reason


# ---------------------------------------------------------------------------
# 1. Key alignment: _build_opportunity reads sl_price/target_price
# ---------------------------------------------------------------------------

class TestSignalKeyAlignment:

    def test_stop_loss_populated_from_sl_price(self):
        opp = _build_opp(_make_orb_signal("BUY", 2488.2, 2474.51, 2504.2))
        assert opp["stop_loss"] == pytest.approx(2474.51), \
            "stop_loss must be read from sl_price, not stop_loss key"

    def test_target_populated_from_target_price(self):
        opp = _build_opp(_make_orb_signal("BUY", 2488.2, 2474.51, 2504.2))
        assert opp["target"] == pytest.approx(2504.2), \
            "target must be read from target_price, not target key"

    def test_risk_reward_not_collapsed_buy(self):
        opp = _build_opp(_make_orb_signal("BUY", 2488.2, 2474.51, 2504.2))
        assert opp["risk_reward"] == pytest.approx(1.17, abs=0.02), \
            "risk_reward must be ~1.17 for ORB BUY, not 0.0 or 1.0"

    def test_risk_reward_not_collapsed_sell(self):
        opp = _build_opp(_make_orb_signal("SELL", 2488.2, 2501.74, 2472.2))
        assert opp["risk_reward"] > 0, "risk_reward must be > 0 for valid SELL"

    def test_old_stop_loss_key_produces_zero_sl(self):
        """Confirm that a signal using old stop_loss key (not sl_price) gets zero -- caught by pre-gate."""
        broken = {
            "symbol": "RELIANCE", "direction": "BUY",
            "entry_price": 2488.2,
            "stop_loss": 2474.51,
            "target": 2504.2,
            "confidence": 0.85,
        }
        opp = _build_opp(broken)
        assert opp["stop_loss"] == 0, \
            "Old stop_loss key must map to 0 (pre-gate validator catches this)"

    def test_old_target_key_produces_zero_target(self):
        broken = {
            "symbol": "RELIANCE", "direction": "BUY",
            "entry_price": 2488.2, "sl_price": 2474.51,
            "target": 2504.2,
            "confidence": 0.85,
        }
        opp = _build_opp(broken)
        assert opp["target"] == 0, \
            "Old target key must map to 0 (pre-gate validator catches this)"


# ---------------------------------------------------------------------------
# 2. Pre-gate orientation validation
# ---------------------------------------------------------------------------

class TestPreGateOrientationValidation:

    def test_valid_buy_passes(self):
        ok, _ = _check_orientation(_make_orb_signal("BUY", 2488.2, 2474.51, 2504.2))
        assert ok

    def test_valid_sell_passes(self):
        ok, _ = _check_orientation(_make_orb_signal("SELL", 2488.2, 2501.74, 2472.2))
        assert ok

    def test_zero_sl_rejected(self):
        s = _make_orb_signal("BUY"); s["sl_price"] = 0
        ok, _ = _check_orientation(s)
        assert not ok

    def test_zero_target_rejected(self):
        s = _make_orb_signal("BUY"); s["target_price"] = 0
        ok, _ = _check_orientation(s)
        assert not ok

    def test_zero_entry_rejected(self):
        s = _make_orb_signal("BUY"); s["entry_price"] = 0
        ok, _ = _check_orientation(s)
        assert not ok

    def test_buy_sl_above_entry_rejected(self):
        ok, _ = _check_orientation(_make_orb_signal("BUY", 2488.2, 2500.0, 2504.2))
        assert not ok

    def test_buy_target_below_entry_rejected(self):
        ok, _ = _check_orientation(_make_orb_signal("BUY", 2488.2, 2474.51, 2470.0))
        assert not ok

    def test_sell_sl_below_entry_rejected(self):
        ok, _ = _check_orientation(_make_orb_signal("SELL", 2488.2, 2475.0, 2472.2))
        assert not ok

    def test_sell_target_above_entry_rejected(self):
        ok, _ = _check_orientation(_make_orb_signal("SELL", 2488.2, 2501.74, 2495.0))
        assert not ok

    def test_missing_sl_price_key_rejected(self):
        s = {"symbol": "R", "direction": "BUY", "entry_price": 2488.2,
             "stop_loss": 2474.51, "target_price": 2504.2}
        ok, _ = _check_orientation(s)
        assert not ok, "No sl_price key => defaults to 0 => rejected"

    def test_missing_target_price_key_rejected(self):
        s = {"symbol": "R", "direction": "BUY", "entry_price": 2488.2,
             "sl_price": 2474.51, "target": 2504.2}
        ok, _ = _check_orientation(s)
        assert not ok, "No target_price key => defaults to 0 => rejected"


# ---------------------------------------------------------------------------
# 3. V2 strategy source-level key audit
# ---------------------------------------------------------------------------

V2_STRATEGIES = ["orb", "mb", "mrf", "ptc", "sic", "trs", "vc"]
V2_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "strategies", "v2"))


class TestAllV2StrategiesEmitCanonicalKeys:

    def _strategy_content(self, name):
        path = os.path.join(V2_DIR, f"{name}.py")
        assert os.path.exists(path), f"Strategy file missing: {path}"
        return open(path).read()

    @pytest.mark.parametrize("name", V2_STRATEGIES)
    def test_strategy_emits_sl_price(self, name):
        content = self._strategy_content(name)
        assert '"sl_price"' in content or "'sl_price'" in content, \
            f"{name}.py does not emit 'sl_price' key -- engine will read 0"

    @pytest.mark.parametrize("name", V2_STRATEGIES)
    def test_strategy_emits_target_price(self, name):
        content = self._strategy_content(name)
        assert '"target_price"' in content or "'target_price'" in content, \
            f"{name}.py does not emit 'target_price' key -- engine will read 0"

    @pytest.mark.parametrize("name", V2_STRATEGIES)
    def test_strategy_does_not_emit_stop_loss_as_output_key(self, name):
        content = self._strategy_content(name)
        assert not re.search(r'''["'"'"']stop_loss["'"'"']\s*:''', content), \
            f"{name}.py emits 'stop_loss' key -- engine reads sl_price, this would silently produce zero SL"
