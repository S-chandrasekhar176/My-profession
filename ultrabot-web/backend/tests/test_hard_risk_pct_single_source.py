"""
Permanent regression tests for the hard_risk_pct single-source-of-truth fix
(audit claim #5).

``hard_risk_pct`` is historically defined in TWO config sections and read by
TWO consumers:
  * risk.hard_pct          → G17CostPreCheck (fee-vs-risk budget)
  * position_sizing.hard_risk_pct → PositionSizer   (hard quantity floor)

Before v0.4.3 nothing enforced agreement after a MANUAL yaml edit, and the
sizer cached its value at construction (so even API dual-writes didn't reach
a running sizer until restart, while G17's rebuilt gates picked them up
immediately — a mid-session divergence window).

The fix has two legs, both pinned here:
  1. Settings._enforce_hard_risk_sync() — risk section canonical; divergence
     → loud warning + in-memory sync; one-sided keys → backfill; runs on
     load AND on every save().
  2. PositionSizer.hard_risk_pct is a live-reading property → running sizer
     tracks its (shared) config dict without restart.
"""
import logging

import pytest
import yaml

from config.settings import Settings
from risk.gates.g17_cost_precheck import G17CostPreCheck
from risk.position_sizer import PositionSizer


CAPITAL_CONFIG = {
    "virtual_capital": 100000,
    "max_capital_usage_pct": 90,
    "min_position_size": 5000,
    "max_per_position_pct": 25,
}


def _settings_with(raw: dict) -> Settings:
    """A Settings instance whose _raw_config is replaced by `raw` (the real
    defaults.yaml load already happened; we only exercise the sync method)."""
    s = Settings()
    s._raw_config = raw
    return s


# ---------------------------------------------------------------------------
# 1. Settings sync engine
# ---------------------------------------------------------------------------
class TestHardRiskSync:
    def test_divergent_values_sync_to_risk_with_loud_warning(self, caplog):
        raw = {"risk": {"hard_risk_pct": 1.5}, "position_sizing": {"hard_risk_pct": 2.5}}
        s = _settings_with(raw)
        with caplog.at_level(logging.WARNING, logger="config.settings"):
            s._enforce_hard_risk_sync()
        assert raw["position_sizing"]["hard_risk_pct"] == 1.5, "risk section must win"
        assert raw["risk"]["hard_risk_pct"] == 1.5
        assert any("CONFIG INCONSISTENCY" in r.message for r in caplog.records), (
            "divergence must produce a LOUD warning, not a silent fix"
        )

    def test_equal_values_no_warning_no_change(self, caplog):
        raw = {"risk": {"hard_risk_pct": 1.5}, "position_sizing": {"hard_risk_pct": 1.5}}
        s = _settings_with(raw)
        with caplog.at_level(logging.WARNING, logger="config.settings"):
            s._enforce_hard_risk_sync()
        assert raw["position_sizing"]["hard_risk_pct"] == 1.5
        assert not any("CONFIG INCONSISTENCY" in r.message for r in caplog.records)

    def test_only_risk_has_key_backfills_position_sizing(self):
        raw = {"risk": {"hard_risk_pct": 1.2}, "position_sizing": {"kelly_max_fraction": 0.08}}
        _settings_with(raw)._enforce_hard_risk_sync()
        assert raw["position_sizing"]["hard_risk_pct"] == 1.2
        assert raw["risk"]["hard_risk_pct"] == 1.2

    def test_only_position_sizing_has_key_backfills_risk(self):
        """User intent from the legacy section is preserved, not discarded."""
        raw = {"risk": {"max_open_positions": 3}, "position_sizing": {"hard_risk_pct": 0.8}}
        _settings_with(raw)._enforce_hard_risk_sync()
        assert raw["risk"]["hard_risk_pct"] == 0.8
        assert raw["position_sizing"]["hard_risk_pct"] == 0.8

    def test_both_missing_is_noop(self):
        raw = {"risk": {"max_open_positions": 3}, "position_sizing": {"kelly_max_fraction": 0.08}}
        _settings_with(raw)._enforce_hard_risk_sync()  # must not raise
        assert "hard_risk_pct" not in raw["risk"]
        assert "hard_risk_pct" not in raw["position_sizing"]

    def test_sections_entirely_missing_is_noop(self):
        _settings_with({"app": {"name": "x"}})._enforce_hard_risk_sync()  # must not raise

    def test_unparseable_value_does_not_crash_or_corrupt(self):
        raw = {"risk": {"hard_risk_pct": "garbage"}, "position_sizing": {"hard_risk_pct": 1.5}}
        _settings_with(raw)._enforce_hard_risk_sync()  # must not raise
        # garbage value must NOT be propagated into position_sizing
        assert raw["position_sizing"]["hard_risk_pct"] == 1.5

    def test_float_equivalence_no_false_warning(self):
        """1.5 vs 1.50 must be treated as equal (float compare, not identity)."""
        raw = {"risk": {"hard_risk_pct": 1.5}, "position_sizing": {"hard_risk_pct": 1.50}}
        _settings_with(raw)._enforce_hard_risk_sync()
        assert raw["position_sizing"]["hard_risk_pct"] == 1.5

    def test_save_persists_synced_values(self, tmp_path, monkeypatch):
        """A hand-edited divergence must never reach disk: save() re-enforces
        the sync right before writing."""
        raw = {"risk": {"hard_risk_pct": 1.5}, "position_sizing": {"hard_risk_pct": 2.5}}
        s = _settings_with(raw)
        out = tmp_path / "out.yaml"
        monkeypatch.setattr(s, "_yaml_path", out)
        assert s.save() is True
        with open(out) as f:
            persisted = yaml.safe_load(f)
        assert persisted["risk"]["hard_risk_pct"] == 1.5
        assert persisted["position_sizing"]["hard_risk_pct"] == 1.5


# ---------------------------------------------------------------------------
# 2. Shipped-config guard (real defaults.yaml)
# ---------------------------------------------------------------------------
class TestShippedConfigGuard:
    def test_shipped_defaults_have_equal_hard_risk_pct(self):
        from config.settings import settings as live_settings

        risk_val = live_settings.get_risk_config().get("hard_risk_pct")
        ps_val = live_settings.get_position_sizing_config().get("hard_risk_pct")
        assert risk_val is not None and ps_val is not None, (
            "both sections must define hard_risk_pct in the shipped defaults.yaml"
        )
        assert float(risk_val) == float(ps_val), (
            f"shipped config diverges: risk={risk_val} position_sizing={ps_val}"
        )


# ---------------------------------------------------------------------------
# 3. PositionSizer live property
# ---------------------------------------------------------------------------
class TestSizerLiveProperty:
    def _signal(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            symbol="UNKNOWN", confidence=0.8, entry_price=1000.0, sl_price=970.0
        )

    def test_property_reads_init_value(self):
        sizer = PositionSizer({"hard_risk_pct": 1.0}, CAPITAL_CONFIG)
        assert sizer.hard_risk_pct == 1.0

    def test_property_tracks_live_config_mutation(self):
        """The running sizer must see API/config updates WITHOUT re-init —
        previously the value was frozen at construction until restart."""
        cfg = {"hard_risk_pct": 1.0}
        sizer = PositionSizer(cfg, CAPITAL_CONFIG)
        assert sizer.hard_risk_pct == 1.0
        cfg["hard_risk_pct"] = 0.5  # simulated live settings update
        assert sizer.hard_risk_pct == 0.5

    def test_property_defaults_to_1_0_when_missing(self):
        sizer = PositionSizer({}, CAPITAL_CONFIG)
        assert sizer.hard_risk_pct == 1.0

    def test_property_defaults_to_1_0_when_unparseable(self):
        sizer = PositionSizer({"hard_risk_pct": "not-a-number"}, CAPITAL_CONFIG)
        assert sizer.hard_risk_pct == 1.0

    def test_hard_floor_enforces_live_value_in_calculate(self):
        """End-to-end with the floor genuinely BINDING (kelly would allow 400):
        capital 1M, entry 200, SL 160 → risk/unit 40, conf 0.95 → kelly qty
        400. hard_risk_pct=1.0 → budget 10000 → floor caps qty to 250; after a
        LIVE config change to 0.5 → budget 5000 → qty 125. Same sizer instance,
        no re-init, no restart."""
        capital = {"virtual_capital": 1_000_000, "max_capital_usage_pct": 90,
                   "min_position_size": 5000, "max_per_position_pct": 25}
        cfg = {"hard_risk_pct": 1.0, "kelly_max_fraction": 0.08}
        sizer = PositionSizer(cfg, capital)
        from types import SimpleNamespace
        sig = SimpleNamespace(symbol="UNKNOWN", confidence=0.95,
                              entry_price=200.0, sl_price=160.0)
        ctx = {"vix": 12.0, "current_drawdown_pct": 1.0, "available_capital": 1_000_000.0}

        r1 = sizer.calculate(sig, ctx)
        assert r1.quantity == 250, (
            f"1.0% of 1M / 40 per unit = 250 (kelly wanted 400), got {r1.quantity}"
        )
        assert r1.risk_amount <= 10_000.0

        cfg["hard_risk_pct"] = 0.5  # live tightening — no re-init, no restart
        r2 = sizer.calculate(sig, ctx)
        assert r2.quantity == 125, (
            f"0.5% of 1M / 40 per unit = 125, got {r2.quantity} "
            f"(live property must feed the floor immediately)"
        )
        assert r2.risk_amount <= 5_000.0


# ---------------------------------------------------------------------------
# 4. Cross-consumer agreement (the actual harm claimed)
# ---------------------------------------------------------------------------
class TestConsumerAgreement:
    def test_g17_and_sizer_agree_after_sync(self, caplog):
        """The exact divergence scenario from the audit: hand-edited YAML with
        risk=1.5, position_sizing=2.5. After the sync, G17's budget and the
        sizer's floor read the SAME number."""
        risk_cfg = {"hard_risk_pct": 1.5, "max_fee_pct_of_risk": 30.0}
        ps_cfg = {"hard_risk_pct": 2.5}
        s = _settings_with({"risk": risk_cfg, "position_sizing": ps_cfg})
        with caplog.at_level(logging.WARNING, logger="config.settings"):
            s._enforce_hard_risk_sync()

        g17 = G17CostPreCheck(risk_cfg)
        sizer = PositionSizer(ps_cfg, CAPITAL_CONFIG)
        assert g17.hard_risk_pct == sizer.hard_risk_pct == 1.5

    def test_g17_and_sizer_agree_on_live_api_update(self):
        """Simulate PUT /api/risk/limits dual-write on the SHARED dicts: both
        consumers must see the new value immediately (G17 via gate rebuild,
        sizer via the live property)."""
        risk_cfg = {"hard_risk_pct": 1.5}
        ps_cfg = {"hard_risk_pct": 1.5}
        # API route dual-writes both sections:
        risk_cfg["hard_risk_pct"] = 0.75
        ps_cfg["hard_risk_pct"] = 0.75

        g17 = G17CostPreCheck(risk_cfg)  # rebuilt per validate()
        sizer = PositionSizer(ps_cfg, CAPITAL_CONFIG)  # even a STALE instance:
        assert g17.hard_risk_pct == 0.75
        assert sizer.hard_risk_pct == 0.75
