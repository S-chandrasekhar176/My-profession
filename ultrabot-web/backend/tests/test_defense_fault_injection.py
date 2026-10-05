"""Defense fault-injection tests: does the engine actually go DEFENSIVE
when the market turns hostile?

The bearish/volatile gap strategy work assumes these walls hold — this file
proves it instead of assuming it:

  1. G7 VIX filter      — VIX above threshold blocks new signals; extreme
                          VIX blocks with critical severity.
  2. Regime flip        — _update_regime_simple fallback rules classify
                          VIX/change correctly AND swap active_strategies
                          to the new regime's roster.
  3. Pending sweep      — a regime shift invalidates pending opportunities
                          of paused strategies (REGIME_TREND_SHIFT), and a
                          closed market invalidates everything.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.engine import UltraBotEngine
from risk.gates.g7_vix_filter import G7VIXFilter
from risk.risk_engine import RiskEngine

IST_TZ = "Asia/Kolkata"

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo(IST_TZ)
except Exception:  # pragma: no cover
    IST = None


def _defense_stub():
    """Engine stub with real regime/sweep machinery bound."""
    eng = UltraBotEngine.__new__(UltraBotEngine)
    eng.config = MagicMock()
    eng.current_regime = "Bull"
    eng.regime_confidence = 0.0
    eng.nifty_price = 23500.0
    eng.nifty_change = 0.0
    eng.vix = 13.0
    eng.regime_detector = None
    eng.adaptive_manager = None
    eng.active_strategies = ["ORB", "PTC", "VC", "SIC"]
    eng.pending_opportunities = {}
    eng.invalidated_opportunities = {}
    eng._opportunities_lock = __import__("asyncio").Lock()
    eng._broadcast = AsyncMock(return_value=None)
    eng.alert_manager = None
    eng.market_hours = None
    eng.feed = None
    eng.broker = None
    eng._repo_getter = None  # _repo_context yields None
    # bind real machinery
    eng._update_regime_simple = UltraBotEngine._update_regime_simple.__get__(eng)
    eng._repo_context = UltraBotEngine._repo_context.__get__(eng)
    eng._validate_pending_opportunities = UltraBotEngine._validate_pending_opportunities.__get__(eng)
    return eng


# ---------------------------------------------------------------------------
# 1. G7 VIX filter
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_g7_blocks_above_threshold():
    gate = G7VIXFilter({"vix_threshold": 22.0})
    result = await gate.check({}, {"vix": 25.0})
    assert result.passed is False
    assert result.gate_name == "G7_VIXFilter"


@pytest.mark.asyncio
async def test_g7_extreme_vix_is_critical():
    gate = G7VIXFilter({"vix_threshold": 22.0})
    result = await gate.check({}, {"vix": 36.0})
    assert result.passed is False
    assert result.severity == "critical"


@pytest.mark.asyncio
async def test_g7_below_threshold_passes():
    gate = G7VIXFilter({"vix_threshold": 22.0})
    result = await gate.check({}, {"vix": 15.0})
    assert result.passed is True


@pytest.mark.asyncio
async def test_g7_fails_open_on_missing_vix():
    """Missing VIX data must not hard-block (data-quality, not risk)."""
    gate = G7VIXFilter({})
    result = await gate.check({}, {})
    assert result.passed is True


@pytest.mark.asyncio
async def test_vix_spike_blocks_through_full_validate():
    """End-to-end: a hostile-VIX signal dies at G7 inside the full gate chain."""
    engine = RiskEngine({})
    sig = {
        "symbol": "RELIANCE", "direction": "BUY", "entry_price": 2500.0,
        "stop_loss": 2480.0, "target": 2550.0, "confidence": 0.85,
        "strategy": "ORB",
    }
    context = {
        "vix": 25.0,
        "total_capital": 100000.0,
        "available_capital": 90000.0,
        "current_price": 2500.0,
    }
    result = await engine.validate(sig, context)
    assert result.passed is False
    assert result.blocked_by == "G7_VIXFilter"


# ---------------------------------------------------------------------------
# 2. Regime flip → defensive roster swap
# ---------------------------------------------------------------------------

def _activation_config():
    """Mirror of defaults.yaml strategy_activation (subset)."""
    return {
        "strategy_activation": {
            "Bull": {"active": ["ORB", "PTC", "VC", "SIC", "MB", "MRF"]},
            "Bear": {"active": ["ORB", "PTC", "VC", "SIC", "MB", "MRF", "TRS"]},
            "Sideways": {"active": ["ORB", "MRF", "VC", "SIC"],
                          "paused": ["PTC", "MB", "TRS", "VR", "BBR"]},
            "Volatile": {"active": ["ORB", "SIC", "TRS"],
                          "paused": ["MB", "PTC", "VC", "MRF"]},
        }
    }


def test_regime_flip_volatile_on_vix_spike():
    """VIX 25 + flat Nifty → Volatile; roster shrinks to ORB/SIC/TRS."""
    eng = _defense_stub()
    eng.config.get_regime_config = MagicMock(return_value={})
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.vix = 25.0
    eng.nifty_change = 0.0
    eng._update_regime_simple()
    assert eng.current_regime == "Volatile"
    assert set(eng.active_strategies) == {"ORB", "SIC", "TRS"}


def test_regime_flip_bear_on_gap_down():
    """Nifty −1.0% → Bear; MRF/MB weights drop, TRS joins the roster."""
    eng = _defense_stub()
    eng.config.get_regime_config = MagicMock(return_value={})
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.vix = 15.0
    eng.nifty_change = -1.0
    eng._update_regime_simple()
    assert eng.current_regime == "Bear"
    assert "TRS" in eng.active_strategies


def test_regime_flip_sideways_vix_12():
    eng = _defense_stub()
    eng.config.get_regime_config = MagicMock(return_value={})
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.vix = 12.0
    eng.nifty_change = 0.1
    eng._update_regime_simple()
    assert eng.current_regime == "Sideways"


def test_regime_flip_bull_on_strength():
    eng = _defense_stub()
    eng.config.get_regime_config = MagicMock(return_value={})
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.vix = 12.0
    eng.nifty_change = 0.8
    eng._update_regime_simple()
    assert eng.current_regime == "Bull"


def test_regime_detector_override_wins_over_fallback():
    """When RegimeDetector classifies, its verdict replaces the fallback rules."""
    eng = _defense_stub()
    eng.regime_detector = MagicMock()
    eng.regime_detector.classify = MagicMock(
        return_value={"regime": "Bear", "confidence": 0.82}
    )
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.vix = 12.0       # fallback rules would say Sideways
    eng.nifty_change = 0.1
    eng._update_regime_simple()
    assert eng.current_regime == "Bear"
    assert eng.regime_confidence == 0.82


# ---------------------------------------------------------------------------
# 3. Pending-opportunity sweep under hostile conditions
# ---------------------------------------------------------------------------

def _fresh_opp(opp_id, strategy="PTC"):
    return (opp_id, {
        "id": opp_id,
        "signal_id": f"sig-{opp_id}",
        "symbol": "TCS",
        "direction": "BUY",
        "strategy": strategy,
        "entry_price": 3500.0,
        "stop_loss": 3465.0,
        "target": 3560.0,
        "quantity": 10,
        "created_at": datetime.now(IST).isoformat() if IST else datetime.now().isoformat(),
    })


class _RepoCtx:
    def __init__(self, repo):
        self._repo = repo

    async def __aenter__(self):
        return self._repo

    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.mark.asyncio
async def test_regime_shift_invalidates_paused_strategy_cards():
    """Bull→Sideways flip: a pending PTC card must be invalidated with
    REGIME_TREND_SHIFT, not silently left confirmable."""
    eng = _defense_stub()
    eng.current_regime = "Sideways"  # flipped after the card was created
    eng.config.get_risk_config = MagicMock(return_value={})
    eng.config.get_strategy_activation = MagicMock(
        side_effect=lambda r: _activation_config()["strategy_activation"].get(r, {})
    )
    eng.feed = MagicMock()
    eng.feed.get_latest_price = AsyncMock(return_value=3500.0)  # no drift/SL/target checks fire
    repo = MagicMock()
    repo.update_signal = AsyncMock(return_value=None)
    eng._repo_context = MagicMock(return_value=_RepoCtx(repo))

    opp_id, opp = _fresh_opp("opp-defense-1")
    eng.pending_opportunities[opp_id] = opp

    await eng._validate_pending_opportunities()

    assert opp_id not in eng.pending_opportunities
    assert repo.update_signal.await_count >= 1


@pytest.mark.asyncio
async def test_closed_market_invalidates_all_cards():
    """Market session closed → every intraday card dies (no overnight risk)."""
    eng = _defense_stub()
    eng.config.get_risk_config = MagicMock(return_value={})
    mh = MagicMock()
    mh.is_market_open = MagicMock(return_value=False)
    eng.market_hours = mh
    repo = MagicMock()
    repo.update_signal = AsyncMock(return_value=None)
    eng._repo_context = MagicMock(return_value=_RepoCtx(repo))

    for i in range(3):
        _, opp = _fresh_opp(f"opp-mh-{i}")
        eng.pending_opportunities[f"opp-mh-{i}"] = opp

    await eng._validate_pending_opportunities()

    assert eng.pending_opportunities == {}
