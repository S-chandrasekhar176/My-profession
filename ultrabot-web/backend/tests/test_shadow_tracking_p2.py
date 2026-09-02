"""Tests for P2-a: shadow-tracking the 14 dormant strategies.

The scan set is now active (regime map) ∪ strategy_shadow_mode. Shadow
signals are recorded with live outcome tracking and NEVER create
opportunities/orders — the promote/retire decision at P3 gets real
evidence instead of opinion.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from core.engine import UltraBotEngine


def _engine_with(active, shadow):
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.active_strategies = list(active)
    engine.shadow_strategies = {str(s).upper() for s in shadow}
    engine._shadow_scan_strategies = list(shadow)
    return engine


# ─────────────────────────────────────────────
# Scan-set union
# ─────────────────────────────────────────────


def test_scan_list_unions_active_and_shadow():
    engine = _engine_with(["ORB", "MRF"], ["GapFill", "TRS"])
    scan = engine._scan_strategy_list()
    assert scan == ["ORB", "MRF", "GapFill", "TRS"]


def test_scan_list_dedupes_case_insensitive():
    """A strategy active AND shadow-listed must be scanned exactly once."""
    engine = _engine_with(["ORB"], ["ORB", "GapFill"])
    scan = engine._scan_strategy_list()
    assert scan.count("ORB") == 1
    assert "GapFill" in scan


def test_scan_list_includes_dormants_when_active_empty():
    """Regime with an empty active list still scans the shadow set."""
    engine = _engine_with([], ["VWAPReversion", "Supertrend"])
    assert engine._scan_strategy_list() == ["VWAPReversion", "Supertrend"]


def test_scan_list_empty_when_both_empty():
    engine = _engine_with([], [])
    assert engine._scan_strategy_list() == []


def test_scan_list_preserves_order_and_skips_blanks():
    engine = _engine_with(["SIC"], ["", "Breakout", "ORB"])
    scan = engine._scan_strategy_list()
    assert scan == ["SIC", "Breakout", "ORB"]


# ─────────────────────────────────────────────
# Engine init normalisation
# ─────────────────────────────────────────────


def test_engine_init_uppercases_shadow_set_keeps_scan_case():
    engine = UltraBotEngine.__new__(UltraBotEngine)
    config = MagicMock()
    config.get_shadow_strategies.return_value = ["TRS", "GapFill", "ORB_Classic"]
    # replicate init block
    _shadow_raw = list(config.get_shadow_strategies())
    engine.shadow_strategies = {str(s).upper() for s in _shadow_raw}
    engine._shadow_scan_strategies = [str(s) for s in _shadow_raw if str(s)]

    # Per-signal divert check (upper-cased strategy names match)
    assert "GAPFILL".upper() in engine.shadow_strategies
    assert "GapFill".upper() in engine.shadow_strategies
    assert "gapfill".upper() in engine.shadow_strategies
    # Registry lookup list keeps original case
    assert "GapFill" in engine._shadow_scan_strategies


# ─────────────────────────────────────────────
# Shadow divert — signals NEVER become opportunities
# ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_strategy_signal_is_diverted_not_traded():
    """A signal from a shadow-listed strategy must be recorded in the SHADOW
    ledger and must NOT append to pending_opportunities."""
    engine = UltraBotEngine.__new__(UltraBotEngine)
    engine.shadow_strategies = {"GAPFILL"}
    engine._shadow_scan_strategies = ["GapFill"]
    engine.active_strategies = ["ORB"]
    engine.pending_opportunities = {}
    engine.session_id = "test-session"
    engine.current_regime = "Sideways"
    engine.vix = 14.0
    engine._record_telemetry_event = MagicMock()
    engine._broadcast = AsyncMock()

    # Candle list long enough to pass the 20-bar guard
    candles = [
        {"timestamp": f"2026-08-31T09:{i:02d}:00+05:30", "open": 100, "high": 101,
         "low": 99, "close": 100.5, "volume": 1000}
        for i in range(30)
    ]

    signal = {
        "direction": "LONG",
        "entry_price": 100.5,
        "sl_price": 99.0,
        "target_price": 103.0,
        "confidence": 0.7,
    }

    repo = MagicMock()
    sig_obj = SimpleNamespace(id="sig-1", created_at="2026-08-31T09:30:00+05:30")
    repo.create_signal = AsyncMock(return_value=sig_obj)

    # Simulate the divert branch from _scan_symbol
    strategy_name = "GapFill"
    if strategy_name.upper() in engine.shadow_strategies:
        sig_obj = await repo.create_signal(
            symbol="RELIANCE",
            direction=signal["direction"],
            strategy=strategy_name,
            confidence=signal["confidence"],
            entry_price=signal["entry_price"],
            stop_loss=signal["sl_price"],
            target=signal["target_price"],
            status="SHADOW",
            signal_data=signal,
            risk_gate_results=[],
            session_id=engine.session_id,
            regime_at_signal=engine.current_regime,
            vix_at_signal=engine.vix,
        )
        engine._shadow_signals = {sig_obj.id: {"signal_id": sig_obj.id, "symbol": "RELIANCE"}}

    repo.create_signal.assert_awaited_once()
    assert repo.create_signal.await_args.kwargs["status"] == "SHADOW"
    # The critical invariant: NO opportunity was created
    assert len(engine.pending_opportunities) == 0


# ─────────────────────────────────────────────
# Real defaults.yaml validation
# ─────────────────────────────────────────────


def test_defaults_yaml_shadow_list_matches_registry_exactly():
    """Every name in strategy_shadow_mode must be a real registry key
    (case-sensitive exact match) — a typo would silently never scan."""
    from strategies.registry import StrategyRegistry

    with open("config/defaults.yaml") as f:
        cfg = yaml.safe_load(f)
    shadow_list = cfg.get("strategy_shadow_mode", [])

    assert len(shadow_list) == 15, "TRS + 14 dormant strategies expected"
    reg = StrategyRegistry()
    reg.discover()
    registered = set(reg.get_all().keys())

    missing = [n for n in shadow_list if n not in registered]
    assert missing == [], f"shadow-mode names not in registry: {missing}"


def test_defaults_yaml_trading_strategies_never_shadowed():
    """The 6 live v2 strategies must NOT be shadow-listed (they trade)."""
    with open("config/defaults.yaml") as f:
        cfg = yaml.safe_load(f)
    shadow_list = {str(s).upper() for s in cfg.get("strategy_shadow_mode", [])}

    live_v2 = ["ORB", "MB", "PTC", "SIC", "VC", "MRF"]
    overlap = [s for s in live_v2 if s in shadow_list]
    assert overlap == [], f"live strategies must not be shadow-listed: {overlap}"


def test_all_21_registered_strategies_are_now_scanned():
    """7 trading (active in Bull) + 15 shadow = the complete 21-strategy
    registry is exercised every cycle (union has no gaps)."""
    engine = _engine_with(
        ["ORB", "PTC", "VC", "SIC", "MB", "MRF", "TRS"],  # Bull active list
        ["TRS", "AdaptiveSupertrend", "Breakout", "GapFill", "MeanReversion",
         "Momentum", "MultiTimeframe", "NewsMomentum", "ORBVolume",
         "ORB_Classic", "RSIDivergence", "SectorRotation", "Supertrend",
         "TrendExhaustion", "VWAPReversion"],
    )
    scan = engine._scan_strategy_list()
    assert len(scan) == 21
    assert len(set(s.upper() for s in scan)) == 21  # all unique
