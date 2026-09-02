"""
WIRING CONTRACT TEST (v0.4.3) — the systemic fix for the "dead gate" class.

Root cause this guards against (audit claim #3 and the G16 incident): gates
are unit-tested with FIXTURE contexts that supply keys the production engine
never actually sends — tests stay green while the gate is inert in live
trading. G16's counter-trend protection was dead in production for exactly
this reason (nobody ever populated context["trend"]).

How this test stays self-maintaining:
  * It SCANS the gate sources (risk/gates/g*.py + risk/risk_engine.py) for
    every context key any gate reads (`.get("K")` / `["K"]`).
  * It builds the REAL engine context via _build_risk_context().
  * Every discovered key must either (a) be present in the engine-built
    context, or (b) be explicitly listed in OPTIONAL_KEYS with a reason.
  * Adding a new gate or a new context.get key WITHOUT supplying it from the
    engine (or documenting it here) FAILS this test — forcing a conscious
    wiring decision instead of a silent dead gate.
"""
import re
from pathlib import Path
from datetime import datetime, time
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")

# Keys gates MAY read that the engine's risk context legitimately does NOT
# supply (each with the documented reason it is safe). Keep this list SHORT
# — every entry is a place a future dead-gate bug could hide.
OPTIONAL_KEYS: dict = {
    "position_value": "G3 computes it from entry_price × quantity when absent",
    "quantity": "signal-scoped; G3/G12 default to 1 / instrument lot size",
    "entry_price": "G9/G12 fall back to current_price/ltp/broker_ltp",
    "volume_ratio": "G15 scanner enrichment — absent means 'no data, skip'",
    "volume": "G15 optional (guarded by explicit 'in' checks)",
    "avg_volume": "G15 optional (guarded by explicit 'in' checks)",
    "segment": "G12 signal-scoped routing option (EQ/FUT/OPT)",
    "product_type": "G12 signal-scoped margin option",
    "order_type": "G12 signal-scoped option",
    "capital_in_use": "G12 derives it from total − available when absent",
    "backtest_result": "G14: explicit backtest payload, optional by design "
    "(live strategy_stats is the primary real source)",
    "max_open_positions": "G2 threshold override channel; config is primary",
    "drawdown_pct": "risk_engine fallback alias — setdefault'd during enrichment",
    "max_drawdown_pct": "risk_engine last-resort fallback — never required",
}

_GET_KEY_RE = re.compile(r'(?:context|ctx)\.get\(\s*["\']([a-zA-Z_0-9]+)["\']')
_DIRECT_KEY_RE = re.compile(r'context\[\s*["\']([a-zA-Z_0-9]+)["\']\s*\]')


def _gate_source_paths() -> list:
    gates_dir = Path(__file__).resolve().parents[1] / "risk" / "gates"
    paths = sorted(gates_dir.glob("g*.py"))
    assert len(paths) >= 18, f"expected the 18 risk gates, found {len(paths)}"
    paths.append(gates_dir.parents[0] / "risk_engine.py")
    return paths


def _scan_gate_context_keys() -> dict:
    """Map: context key -> list of source files that read it."""
    key_sources: dict = {}
    for path in _gate_source_paths():
        src = path.read_text()
        # strip comments so commented-out code can't fake a requirement
        no_comments = re.sub(r"#.*", "", src)
        for key in _GET_KEY_RE.findall(no_comments) + _DIRECT_KEY_RE.findall(no_comments):
            key_sources.setdefault(key, []).append(path.name)
    return key_sources


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


async def _real_context(engine, regime="Bear"):
    engine.current_regime = regime
    return await engine._build_risk_context(
        {"strategy": "ORB", "direction": "BUY"}, "RELIANCE", 2500.0, open_positions=[]
    )


# ---------------------------------------------------------------------------
# 1. THE CONTRACT: every gate-read key is supplied or consciously optional
# ---------------------------------------------------------------------------
class TestWiringContract:
    @pytest.mark.asyncio
    async def test_every_gate_context_key_is_supplied_or_documented(self):
        ctx = await _real_context(_make_engine())
        key_sources = _scan_gate_context_keys()
        assert key_sources, "scan regex rotted — no keys discovered"

        unsupplied = {}
        for key, sources in sorted(key_sources.items()):
            if key not in ctx and key not in OPTIONAL_KEYS:
                unsupplied[key] = sources
        assert not unsupplied, (
            "Risk gates read context keys the production engine NEVER supplies "
            "(the dead-gate failure mode — the gate silently runs on defaults "
            "in live trading while unit tests pass because fixtures provide "
            "the key). Either supply each key from "
            "UltraBotEngine._build_risk_context() or document it in "
            "OPTIONAL_KEYS with a reason:\n  "
            + "\n  ".join(f"{k} (read by {v})" for k, v in unsupplied.items())
        )

    def test_scan_discovers_the_known_wired_keys(self):
        """Sanity: the scan itself is alive (regex can't rot silently)."""
        key_sources = _scan_gate_context_keys()
        for must in ("trend", "open_position_symbols", "vix", "daily_pnl"):
            assert must in key_sources, (
                f"scan no longer discovers {must!r} — update the contract regex"
            )

    def test_optional_keys_are_actually_still_read(self):
        """Every documented optional key must still exist in gate sources —
        stale allowlist entries (gate stopped reading the key) must be pruned
        so this list cannot accumulate hidden dead-gate risks."""
        key_sources = _scan_gate_context_keys()
        stale = [k for k in OPTIONAL_KEYS if k not in key_sources]
        assert not stale, f"OPTIONAL_KEYS contains keys no gate reads any more: {stale}"


# ---------------------------------------------------------------------------
# 2. The specific wiring this release fixed (G16) stays wired
# ---------------------------------------------------------------------------
class TestTrendWiringContract:
    @pytest.mark.asyncio
    async def test_trend_present_valid_and_consistent(self):
        engine = _make_engine()
        ctx = await _real_context(engine, regime="Bear")
        assert ctx["trend"] in ("bull", "bear", "neutral")
        assert ctx["trend"] == engine._regime_to_trend() == "bear"

    @pytest.mark.asyncio
    async def test_nifty_trend_alias_in_sync(self):
        ctx = await _real_context(_make_engine())
        assert ctx["nifty_trend"] == ctx["trend"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("regime", ["Bull", "Bear", "Sideways", "Volatile"])
    async def test_trend_wired_for_all_regimes(self, regime):
        ctx = await _real_context(_make_engine(), regime=regime)
        assert ctx["trend"] in ("bull", "bear", "neutral"), (
            f"regime {regime!r} produced invalid trend {ctx['trend']!r}"
        )


# ---------------------------------------------------------------------------
# 3. Context structural integrity (aliases in lockstep, types sane)
# ---------------------------------------------------------------------------
class TestContextIntegrity:
    @pytest.mark.asyncio
    async def test_open_position_keys_in_lockstep(self):
        ctx = await _real_context(_make_engine())
        assert ctx["open_position_symbols"] == ctx["open_positions_list"] == []
        assert isinstance(ctx["open_position_symbols"], list)

    @pytest.mark.asyncio
    async def test_essential_keys_non_degenerate(self):
        ctx = await _real_context(_make_engine())
        assert float(ctx["total_capital"]) > 0
        assert float(ctx["capital"]) > 0
        assert isinstance(ctx["vix"], (int, float)) and ctx["vix"] >= 0
        assert isinstance(ctx["current_time"], datetime)
        assert isinstance(ctx["daily_trades"], int) and ctx["daily_trades"] >= 0
        assert isinstance(ctx["positions_by_sector"], dict)
        assert ctx["symbol"] == "RELIANCE"
        assert ctx["regime"] == "Bear"

    @pytest.mark.asyncio
    async def test_real_context_satisfies_full_risk_engine_without_error(self):
        """The engine's real context must carry all 18 gates to completion —
        no KeyError/TypeError from a missing key mid-pipeline. In a Bear
        regime a BUY must be blocked BY G16 (proving both completion and the
        live counter-trend protection)."""
        from risk.risk_engine import RiskEngine

        ctx = dict(await _real_context(_make_engine(), regime="Bear"))
        # mid-session timestamp so G8's window passes on weekend test runs
        ctx["current_time"] = datetime.combine(
            datetime.now(IST).date(), time(11, 0), tzinfo=IST
        )

        signal = {
            "symbol": "RELIANCE",
            "strategy": "Momentum",
            "direction": "BUY",
            "confidence": 0.9,
            "entry_price": 2500.0,
            "quantity": 1,
        }
        risk_engine = RiskEngine({"max_open_positions": 3, "max_daily_trades": 10})
        res = await risk_engine.evaluate(signal=signal, symbol="RELIANCE", context=ctx)
        assert res.blocked_by == "G16_MultiTimeframe", (
            f"pipeline completed but blocked_by={res.blocked_by!r} "
            f"({res.block_reason}) — expected the G16 counter-trend block"
        )
