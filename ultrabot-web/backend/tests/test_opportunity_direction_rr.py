"""
Permanent regression tests for the direction-comparison cleanup in
_build_opportunity (audit claim #4).

The live-run-2 correction rule (engine.py, _is_long_direction docstring) is
"EVERY direction branch must go through the helper" — strategies emit
BUY/SELL while legacy code compared == "LONG" exactly, which historically
inverted P&L/SL/target logic for every real position. _build_opportunity's
risk/reward math still contained two raw == "LONG" comparisons; they were
behaviorally masked by abs() wrapping, but any future edit removing the
abs() would have reactivated the inverted-sign bug for every BUY/SELL
opportunity. These tests pin the correct distances/RR for ALL direction
vocabularies so that can never happen silently.
"""
from unittest.mock import MagicMock

import pytest


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
        daily_risk_manager=None,
        broker_factory=MagicMock(),
        feed_manager=MagicMock(),
        session_manager=MagicMock(),
    )


def _build(engine, direction):
    signal = {
        "symbol": "RELIANCE",
        "strategy": "ORB",
        "direction": direction,
        "confidence": 0.8,
        "entry_price": 100.0,
        "sl_price": 95.0,       # 5% below for longs / above for shorts
        "target_price": 110.0,  # 10% above for longs / below for shorts
    }
    return engine._build_opportunity(
        signal=signal,
        strategy_name="ORB",
        symbol="RELIANCE",
        current_price=100.0,
        sizing={"quantity": 0},
        risk_result={"passed": True, "all_gates": []},
    )


class TestOpportunityDirectionRiskReward:
    @pytest.mark.parametrize("direction", ["BUY", "LONG"])
    def test_long_directions(self, direction):
        engine = _make_engine()
        opp = _build(engine, direction)
        assert opp["sl_distance_pct"] == 5.0, f"{direction}: SL distance must be 5%"
        assert opp["target_pct"] == 10.0, f"{direction}: target distance must be 10%"
        assert opp["risk_reward"] == 2.0, f"{direction}: RR must be 10/5 = 2.0"

    @pytest.mark.parametrize("direction", ["SELL", "SHORT"])
    def test_short_directions(self, direction):
        """Shorts carry SL ABOVE entry and target BELOW entry: sl_price=95 is
        5 points on the wrong side — the abs() distance math must still yield
        5% SL / 10% target / RR 2.0 for a consistent setup."""
        engine = _make_engine()
        opp = _build(engine, direction)
        assert opp["sl_distance_pct"] == 5.0, f"{direction}: SL distance must be 5%"
        assert opp["target_pct"] == 10.0, f"{direction}: target distance must be 10%"
        assert opp["risk_reward"] == 2.0, f"{direction}: RR must be 10/5 = 2.0"

    @pytest.mark.parametrize(
        "direction,sl,target,exp_sl_pct,exp_tgt_pct",
        [
            ("BUY", 95.0, 115.0, 5.0, 15.0),   # RR 3.0
            ("SELL", 103.0, 90.0, 3.0, 10.0),  # RR 3.33 → 3.33
        ],
    )
    def test_asymmetric_levels(self, direction, sl, target, exp_sl_pct, exp_tgt_pct):
        """Properly-sided levels for each direction produce the right
        distances (not masked-by-abs artifacts)."""
        engine = _make_engine()
        signal = {
            "symbol": "TCS",
            "strategy": "MB",
            "direction": direction,
            "confidence": 0.8,
            "entry_price": 100.0,
            "sl_price": sl,
            "target_price": target,
        }
        opp = engine._build_opportunity(
            signal=signal,
            strategy_name="MB",
            symbol="TCS",
            current_price=100.0,
            sizing={"quantity": 0},
            risk_result={"passed": True, "all_gates": []},
        )
        assert opp["sl_distance_pct"] == exp_sl_pct
        assert opp["target_pct"] == exp_tgt_pct

    def test_missing_direction_falls_to_short_branch_safely(self):
        """Legacy default path: no direction → SHORT branch + abs() → sane
        distances, never a crash or negative distance."""
        engine = _make_engine()
        opp = _build(engine, None)
        assert opp["sl_distance_pct"] == 5.0
        assert opp["risk_reward"] == 2.0

    def test_zero_sl_distance_gives_zero_rr(self):
        engine = _make_engine()
        signal = {
            "symbol": "X", "strategy": "ORB", "direction": "BUY",
            "confidence": 0.8, "entry_price": 100.0, "sl_price": 100.0,
            "target_price": 110.0,
        }
        opp = engine._build_opportunity(
            signal=signal, strategy_name="ORB", symbol="X", current_price=100.0,
            sizing={"quantity": 0}, risk_result={"passed": True, "all_gates": []},
        )
        assert opp["risk_reward"] == 0.0

    def test_no_raw_long_comparisons_remain_in_source(self):
        """Static guard: no raw `== "LONG"` comparisons may remain in
        engine.py outside comments/docstrings (the live-run-2 rule)."""
        import re
        from pathlib import Path

        src = Path("core/engine.py").read_text()
        # strip comments and docstrings crudely but effectively for this check
        no_comments = re.sub(r"#.*", "", src)
        no_comments = re.sub(r'""".*?"""', "", no_comments, flags=re.DOTALL)
        no_comments = re.sub(r"'''.*?'''", "", no_comments, flags=re.DOTALL)
        offenders = re.findall(r'==\s*"LONG"|==\s*\'LONG\'', no_comments)
        assert not offenders, f"raw == \"LONG\" comparisons must go through _is_long_direction: {offenders}"

    def test_no_attribute_direction_comparisons_anywhere_in_backend(self):
        """Static guard (v0.4.4): no raw attribute-form direction comparisons
        (``pos.direction == "LONG"`` / ``!=`` / reversed) may remain in ANY
        backend production file outside comments/docstrings.

        Rationale: TWO instances of this exact pattern shipped past the
        engine.py-only guard above and silently inverted P&L for BUY
        positions — core/scheduler.py's 15:20 auto-squareoff (recorded a
        +₹50 gain as −₹50, feeding the daily-risk circuit breaker a fake
        loss) and api/routes/dashboard.py's engine-down fallback
        (inverted unrealized P&L). Positions/signal objects come from the
        engine domain and carry BUY/SELL; every comparison must go through
        utils.direction.is_long_direction.

        Modules with a self-contained LONG/SHORT vocabulary (backtest
        simulator, kronos scorer, fee calculator, paper broker's internal
        book) compare LOCAL variables / dict keys, never engine-domain
        attributes, so the attribute-form pattern flags exactly the bug
        class without false positives.
        """
        import re
        from pathlib import Path

        pattern = re.compile(
            r"""[\w\)\]]\s*\.\s*direction\s*[!=]=\s*["'](LONG|SHORT)["']"""
            r"""|["'](LONG|SHORT)["']\s*[!=]=\s*[\w\)\]]\s*\.\s*direction"""
        )
        offenders = []
        for path in Path(".").rglob("*.py"):
            s = str(path)
            if "venv" in s or s.startswith("tests/") or "node_modules" in s:
                continue
            try:
                src = path.read_text()
            except OSError:
                continue
            no_comments = re.sub(r"#.*", "", src)
            no_comments = re.sub(r'""".*?"""', "", no_comments, flags=re.DOTALL)
            no_comments = re.sub(r"'''.*?'''", "", no_comments, flags=re.DOTALL)
            for m in pattern.finditer(no_comments):
                offenders.append(f"{s}: …{m.group(0)!r}…")
        assert not offenders, (
            "raw attribute-form direction comparisons must go through "
            "utils.direction.is_long_direction (see test docstring): "
            f"{offenders}"
        )
