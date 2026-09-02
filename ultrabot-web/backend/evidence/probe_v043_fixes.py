"""
LIVE EVIDENCE PROBE (v0.4.3 fixes) — runs against the backend's REAL modules:
  * real config.settings.settings  (production defaults.yaml, real thresholds)
  * real risk.risk_engine.RiskEngine (all 18 gates, production config)
  * real core.engine.UltraBotEngine._build_risk_context (real context builder)

Only external services (repo/broker/feed) are MagicMocked — Saturday, market
closed, no live data feed needed for wiring proof.
"""
import asyncio
import copy
import io
import logging
import sys
from unittest.mock import MagicMock

sys.path.insert(0, "/home/z/Awesome_DE/ultrabot-web/backend")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

print("=" * 78)
print("PROBE 1 — real Settings: shipped config state")
print("=" * 78)
from config.settings import settings

risk_cfg = settings.get_risk_config()
ps_cfg = settings.get_position_sizing_config()
print(f"risk.hard_risk_pct           = {risk_cfg.get('hard_risk_pct')}")
print(f"position_sizing.hard_risk_pct = {ps_cfg.get('hard_risk_pct')}")
assert float(risk_cfg["hard_risk_pct"]) == float(ps_cfg["hard_risk_pct"]), "shipped config must agree"
print("→ SHIPPED CONFIG CONSISTENT (both sections agree)\n")

print("=" * 78)
print("PROBE 2 — hard_risk sync validator on a HAND-EDITED divergent config")
print("=" * 78)
# Simulate the exact audit scenario: user hand-edits only the risk section
simulated = copy.deepcopy(settings._raw_config)
simulated["risk"]["hard_risk_pct"] = 2.0          # hand-edited
simulated["position_sizing"]["hard_risk_pct"] = 1.5  # left stale
settings._raw_config = simulated

log_capture = io.StringIO()
handler = logging.StreamHandler(log_capture)
handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logging.getLogger("config.settings").addHandler(handler)
logging.getLogger("config.settings").setLevel(logging.WARNING)

settings._enforce_hard_risk_sync()

warning_output = log_capture.getvalue()
print("captured log output:")
print("  " + warning_output.strip().replace("\n", "\n  "))
assert "CONFIG INCONSISTENCY" in warning_output, "loud warning must fire"
assert settings._raw_config["position_sizing"]["hard_risk_pct"] == 2.0, "risk section must win"
assert settings._raw_config["risk"]["hard_risk_pct"] == 2.0
print(f"→ AFTER SYNC: risk={settings._raw_config['risk']['hard_risk_pct']} "
      f"position_sizing={settings._raw_config['position_sizing']['hard_risk_pct']}")
print("→ VALIDATOR WORKS: loud warning + risk-canonical sync\n")

logging.getLogger("config.settings").removeHandler(handler)

print("=" * 78)
print("PROBE 3 — G17 and PositionSizer agree on the REAL config (live property)")
print("=" * 78)
from risk.gates.g17_cost_precheck import G17CostPreCheck
from risk.position_sizer import PositionSizer

# PositionSizer receives the live position_sizing dict reference (as app.py wires it)
live_ps_cfg = settings.get_position_sizing_config()
live_cap_cfg = settings.get_capital_config()
sizer = PositionSizer(live_ps_cfg, live_cap_cfg)
g17 = G17CostPreCheck(settings.get_risk_config())
print(f"G17 budget basis (risk section)   : {g17.hard_risk_pct}")
print(f"Sizer hard floor (position_sizing): {sizer.hard_risk_pct}")
assert g17.hard_risk_pct == sizer.hard_risk_pct
# simulate a live API dual-write — both consumers see it immediately
settings.get_risk_config()["hard_risk_pct"] = 1.2
live_ps_cfg["hard_risk_pct"] = 1.2
g17b = G17CostPreCheck(settings.get_risk_config())
print(f"after live API update to 1.2      : G17(rebuilt)={g17b.hard_risk_pct}  "
      f"Sizer(live property, NO re-init)={sizer.hard_risk_pct}")
assert g17b.hard_risk_pct == sizer.hard_risk_pct == 1.2
print("→ CONSUMERS IN LOCKSTEP, EVEN MID-SESSION (no restart needed)\n")
# restore pristine values
settings.get_risk_config()["hard_risk_pct"] = 1.5
live_ps_cfg["hard_risk_pct"] = 1.5

print("=" * 78)
print("PROBE 4 — G16 fires on the REAL 18-gate pipeline (Bear regime, BUY)")
print("=" * 78)
from core.engine import UltraBotEngine
from risk.risk_engine import RiskEngine
from datetime import datetime, time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
engine = UltraBotEngine(
    config=settings,                      # ← REAL production settings
    repository_getter=MagicMock(),        # external service, mocked
    error_engine=MagicMock(),
    risk_engine=MagicMock(),
    position_sizer=MagicMock(),
    partial_booker=None,
    daily_risk_manager=None,
    broker_factory=MagicMock(),
    feed_manager=MagicMock(),
    session_manager=MagicMock(),
)
engine.current_regime = "Bear"

signal = {
    "symbol": "RELIANCE", "strategy": "Momentum", "direction": "BUY",
    "confidence": 0.9, "entry_price": 2500.0, "quantity": 1,
}
ctx = asyncio.run(engine._build_risk_context(signal, "RELIANCE", 2500.0, open_positions=[]))
print(f"engine regime = {engine.current_regime!r}  →  context['trend'] = {ctx['trend']!r}")
assert ctx["trend"] == "bear"

real_risk_engine = RiskEngine(settings.get_risk_config())  # real production thresholds
print(f"RiskEngine gates armed: {len(real_risk_engine.gates)} (expected 18)")
assert len(real_risk_engine.gates) == 18

# mid-session timestamp so G8's window is satisfied (Saturday probe)
ctx["current_time"] = datetime.combine(datetime.now(IST).date(), time(11, 0), tzinfo=IST)

res = asyncio.run(real_risk_engine.evaluate(signal=signal, symbol="RELIANCE", context=ctx))
print(f"BUY in Bear regime → passed={res.passed}  blocked_by={res.blocked_by}")
print(f"   block_reason: {res.block_reason}")
assert res.passed is False and res.blocked_by == "G16_MultiTimeframe"

# and the aligned direction passes the same pipeline
signal_ok = dict(signal, direction="SELL", strategy="Momentum")
ctx2 = asyncio.run(engine._build_risk_context(signal_ok, "RELIANCE", 2500.0, open_positions=[]))
ctx2["current_time"] = ctx["current_time"]
res2 = asyncio.run(real_risk_engine.evaluate(signal=signal_ok, symbol="RELIANCE", context=ctx2))
print(f"SELL in Bear regime → passed={res2.passed}  blocked_by={res2.blocked_by}")
assert res2.passed is True, f"aligned trade should pass: {res2.block_reason}"
print("→ G16 ALIVE IN PRODUCTION WIRING: counter-trend blocked, aligned passed\n")

print("=" * 78)
print("PROBE 5 — engine raw context keys (G16 wiring + lockstep aliases)")
print("=" * 78)
for k in ("regime", "trend", "nifty_trend", "open_position_symbols", "open_positions_list"):
    print(f"  context[{k!r}] = {ctx[k]!r}")
assert ctx["open_position_symbols"] == ctx["open_positions_list"] == []
assert ctx["nifty_trend"] == ctx["trend"] == "bear"

print()
print("=" * 78)
print("ALL LIVE PROBES PASSED — v0.4.3 fixes verified on real production wiring")
print("=" * 78)
