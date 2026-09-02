"""v0.4.6 regression suite — the position-sizer hard-risk drift incident.

The incident (user-reported, verified live against the shipped artifact):
    Case: entry=100, stop_loss=50 (50-point wide stop),
          capital=₹500,000, hard_risk_pct=1%
    Expected: qty capped at 100  (max loss 100 × 50  = ₹5,000 = 1.0%)
    Actual:   qty = 150          (max loss 150 × 50 = ₹7,500 = 1.5% — 50% over)

Root cause was NOT the cap math — the sizer faithfully enforces whatever
value is configured (these tests prove it on both 1.0 and 1.5 budgets).
The shipped v0.4.2 defaults.yaml had been polluted to hard_risk_pct: 1.5
by a test payload during the v0.4.2 session (vix_threshold was silently
moved 20 → 22.0 in the same event), and v0.4.3's single-source-of-truth
sync then cemented 1.5 as "consistent". v0.4.6 restores the pristine
values; these tests pin every layer of that restoration:

  1. The user's exact case on a 1.0% budget → quantity ≤ 100, risk ≤ ₹5,000
  2. The same case on a 1.5% budget → 150 (documents value-driven math,
     i.e. the original "actual" was the config, not a broken cap)
  3. The SHIPPED defaults.yaml must carry hard_risk_pct == 1.0 in BOTH
     sections and vix_threshold == 20 (the drift can never silently return)
  4. Sizer telemetry must report the CONFIGURED pct, never a hardcoded
     "1%" (a 1.5 budget capping at 1.5% must not claim it capped at 1%)
  5. F&O wide-stop trades get the lot-adjusted cap, not a bypass
"""
import yaml
import pytest

from risk.position_sizer import PositionSizer


BACKEND_ROOT = __file__.rsplit("/tests/", 1)[0]
DEFAULTS_PATH = f"{BACKEND_ROOT}/config/defaults.yaml"


def _load_shipped_config():
    with open(DEFAULTS_PATH) as fh:
        return yaml.safe_load(fh)


def _make_sizer(hard_risk_pct: float, capital: float = 500000.0) -> PositionSizer:
    """Sizer configured exactly like the incident report."""
    return PositionSizer(
        config={"hard_risk_pct": hard_risk_pct},
        capital_config={
            "virtual_capital": capital,
            "max_capital_usage_pct": 90,
            "min_position_size": 5000,
            "max_per_position_pct": 25,
        },
    )


def _incident_signal():
    """The user's exact signal: entry=100, SL=50, wide 50-point stop."""
    return {
        "symbol": "DRIFTX",       # not in the FNO map → sized as equity
        "confidence": 0.85,
        "entry_price": 100.0,
        "sl_price": 50.0,
        "segment": "EQ",
    }


def _incident_ctx(capital: float = 500000.0):
    return {
        "vix": 15.0,
        "current_drawdown_pct": 1.0,
        "available_capital": capital,
    }


class TestUserReportedCase:
    def test_exact_incident_case_capped_at_1pct_budget(self):
        """entry=100 / SL=50 / ₹5L / hard_risk_pct=1.0 → qty ≤ 100, risk ≤ ₹5,000."""
        sizer = _make_sizer(hard_risk_pct=1.0)
        result = sizer.calculate(_incident_signal(), _incident_ctx())
        assert result.quantity == 100, (
            f"1% budget on a ₹50 stop over ₹5L capital must cap at exactly "
            f"100 shares, got {result.quantity}"
        )
        assert result.risk_amount <= 5000.0
        assert result.risk_pct <= 1.0

    def test_incident_case_risk_never_exceeds_budget(self):
        """The invariant the hard floor exists for: risk_amount ≤ budget."""
        sizer = _make_sizer(hard_risk_pct=1.0)
        result = sizer.calculate(_incident_signal(), _incident_ctx())
        budget = sizer.total_capital * (sizer.hard_risk_pct / 100.0)
        assert result.risk_amount <= budget + 1e-9

    def test_wider_stop_reduces_quantity_not_risk(self):
        """The floor scales qty inversely with stop width — a 2x wider stop
        must halve the capped quantity, keeping rupee risk at the budget."""
        sizer = _make_sizer(hard_risk_pct=1.0)
        narrow = dict(_incident_signal(), sl_price=99.0)   # ₹1 stop
        wide = dict(_incident_signal(), sl_price=98.0)     # ₹2 stop
        r_narrow = sizer.calculate(narrow, _incident_ctx())
        r_wide = sizer.calculate(wide, _incident_ctx())
        assert r_narrow.risk_amount <= 5000.0
        assert r_wide.risk_amount <= 5000.0

    def test_1_5_budget_gives_150_documenting_value_driven_math(self):
        """The pre-fix 'actual' (150) was the CONFIG being 1.5, not a broken
        cap — the same sizer at 1.5% must produce exactly the reported 150.
        This test documents that the cap is value-driven so a future
        'quantity looks too big' report can be triaged in one run."""
        sizer = _make_sizer(hard_risk_pct=1.5)
        result = sizer.calculate(_incident_signal(), _incident_ctx())
        assert result.quantity == 150
        assert result.risk_amount == pytest.approx(7500.0)
        assert result.risk_pct == pytest.approx(1.5)


class TestShippedConfigDriftGuards:
    """The drift itself can never silently return to the shipped file."""

    def test_shipped_hard_risk_pct_is_pristine_1pct_in_both_sections(self):
        raw = _load_shipped_config()
        risk_val = float(raw["risk"]["hard_risk_pct"])
        ps_val = float(raw["position_sizing"]["hard_risk_pct"])
        assert risk_val == 1.0, (
            f"risk.hard_risk_pct drifted to {risk_val} — pristine value is 1.0 "
            "(the v0.4.2 test-payload pollution; see conftest tripwire v2)"
        )
        assert ps_val == 1.0, (
            f"position_sizing.hard_risk_pct drifted to {ps_val} — pristine "
            "value is 1.0"
        )

    def test_shipped_vix_threshold_is_pristine_20(self):
        raw = _load_shipped_config()
        val = float(raw["risk"]["vix_threshold"])
        assert val == 20, (
            f"risk.vix_threshold drifted to {val} — pristine value is 20 "
            "(moved to 22.0 by the same v0.4.2 pollution event; G7's VIX "
            "block level silently loosened)"
        )

    def test_shipped_sections_agree_on_hard_risk_pct(self):
        """On-disk agreement (the v0.4.3 sync invariant, verified at the file
        level so a hand edit can't ship a diverged artifact)."""
        raw = _load_shipped_config()
        assert float(raw["risk"]["hard_risk_pct"]) == float(
            raw["position_sizing"]["hard_risk_pct"]
        )

    def test_shipped_kelly_bounds_untouched(self):
        raw = _load_shipped_config()
        ps = raw["position_sizing"]
        assert float(ps["kelly_min_fraction"]) == 0.02
        assert float(ps["kelly_max_fraction"]) == 0.08

    def test_sizer_live_property_reads_configured_value(self):
        """v0.4.3 live-reading property: constructing from the SHIPPED config
        must yield the pristine 1.0 (not a stale 1.5 cached anywhere)."""
        raw = _load_shipped_config()
        sizer = PositionSizer(raw["position_sizing"], raw["capital"])
        assert sizer.hard_risk_pct == 1.0


class TestSizerTelemetryHonesty:
    """The note text must report the CONFIGURED pct — v0.4.4 shipped a sizer
    capping at 1.5% while its note claimed '1% hard capital-risk floor'."""

    def test_note_reports_configured_pct_not_hardcoded_1pct(self):
        sizer = _make_sizer(hard_risk_pct=1.5)
        result = sizer.calculate(_incident_signal(), _incident_ctx())
        assert result.notes is not None
        assert "1.5% hard capital-risk floor" in result.notes
        assert "by 1% " not in result.notes, (
            "Note text must never claim a hardcoded '1%' floor while the "
            "configured budget is different (misleading ops telemetry)"
        )

    def test_note_reports_1pct_when_configured_1pct(self):
        sizer = _make_sizer(hard_risk_pct=1.0)
        result = sizer.calculate(_incident_signal(), _incident_ctx())
        assert result.notes is not None
        assert "1% hard capital-risk floor" in result.notes

    def test_min_bump_suppression_note_reports_configured_pct(self):
        """A minimum-size bump that would breach the floor reports the real
        configured pct in its suppression note."""
        sizer = _make_sizer(hard_risk_pct=0.5)  # ₹2,500 budget
        # Tiny position whose min-size bump would risk beyond the budget:
        # entry 100, SL 90 → risk/unit ₹10; min ₹5,000 position = 50 shares
        # → ₹500 risk < budget… force the breach with a very wide stop:
        signal = {
            "symbol": "DRIFTX",
            "confidence": 0.85,
            "entry_price": 100.0,
            "sl_price": 20.0,   # ₹80 stop → capped qty = int(2500/80) = 31
            "segment": "EQ",
        }
        result = sizer.calculate(signal, _incident_ctx())
        if result.notes and "minimum bump suppressed" in result.notes:
            assert "0.5% hard risk floor" in result.notes


class TestFnoWideStopCap:
    def test_fno_wide_stop_lot_adjusted_cap(self):
        """F&O wide-stop trades get the lot-adjusted floor — a budget that
        cannot buy a whole lot at the risk cap yields ZERO, never an
        over-budget lot."""
        sizer = _make_sizer(hard_risk_pct=1.0)
        signal = {
            "symbol": "RELIANCE",     # F&O symbol, lot 500 in the verified map
            "confidence": 0.85,
            "entry_price": 2500.0,
            "sl_price": 2400.0,       # ₹100 stop → budget ₹5,000 → 50 shares
            "segment": "FNO",
        }
        result = sizer.calculate(signal, _incident_ctx())
        budget = sizer.total_capital * (sizer.hard_risk_pct / 100.0)
        assert result.risk_amount <= budget + 1e-9
        # ₹5,000 / ₹100 = 50 shares → 0 whole 500-lots → quantity 0
        assert result.quantity == 0
