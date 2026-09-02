"""Unit tests for core.capital_resolver and G5 fail-closed integration."""
import pytest
from unittest.mock import MagicMock

from core.capital_resolver import resolve_total_capital
from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
from models.risk_state import GateResult


def test_resolve_default():
    """When no engine, context, or config is provided, resolves to settings value (500,000)."""
    cap = resolve_total_capital()
    assert cap == 500000.0


def test_resolve_from_context_total_capital():
    """Context with total_capital takes top priority."""
    cap = resolve_total_capital(context={"total_capital": 350000.0})
    assert cap == 350000.0


def test_resolve_from_context_capital_alias():
    """Context with 'capital' key resolves correctly."""
    cap = resolve_total_capital(context={"capital": 250000.0})
    assert cap == 250000.0


def test_resolve_from_empty_context():
    """Empty context dict falls back to settings (500,000), not 0 or 100,000."""
    cap = resolve_total_capital(context={})
    assert cap == 500000.0


def test_resolve_explicit_zero_in_context_preserved():
    """Explicit total_capital=0 in context must return 0.0 to allow fail-closed gate evaluation."""
    cap = resolve_total_capital(context={"total_capital": 0.0})
    assert cap == 0.0


def test_resolve_explicit_negative_in_context_preserved():
    """Explicit negative capital in context must return negative to allow fail-closed gate evaluation."""
    cap = resolve_total_capital(context={"total_capital": -50000.0})
    assert cap == -50000.0


def test_resolve_from_engine_instance():
    """Active engine with initial_capital resolves engine's configured capital."""
    mock_engine = MagicMock()
    mock_engine.initial_capital = 750000.0
    cap = resolve_total_capital(engine=mock_engine)
    assert cap == 750000.0


def test_resolve_from_engine_explicit_zero_preserved():
    """Engine with initial_capital=0.0 preserves 0.0."""
    mock_engine = MagicMock()
    mock_engine.initial_capital = 0.0
    cap = resolve_total_capital(engine=mock_engine)
    assert cap == 0.0


def test_resolve_from_custom_config():
    """Custom config object with get_capital_config resolves correctly."""
    mock_cfg = MagicMock()
    mock_cfg.get_capital_config.return_value = {"virtual_capital": 600000.0}
    cap = resolve_total_capital(config=mock_cfg)
    assert cap == 600000.0


def test_resolve_from_custom_config_explicit_zero_preserved():
    """Config with virtual_capital=0 preserves 0.0."""
    mock_cfg = MagicMock()
    mock_cfg.get_capital_config.return_value = {"virtual_capital": 0.0}
    cap = resolve_total_capital(config=mock_cfg)
    assert cap == 0.0


def test_g5_contract_missing_context_resolves_canonical_default():
    """Resolver with missing capital in context resolves to 500,000, allowing loss limit calculation."""
    context = {"daily_pnl": -5000.0}
    total_capital = resolve_total_capital(context=context)
    assert total_capital == 500000.0
    loss_limit = total_capital * (3.0 / 100.0)
    assert loss_limit == 15000.0
    assert context["daily_pnl"] > -loss_limit


def test_g5_contract_explicit_zero_capital_fails_closed():
    """Resolver with explicit capital=0.0 returns 0.0, allowing G5 check total_capital <= 0 to fail closed."""
    context = {"total_capital": 0.0, "daily_pnl": 0.0}
    total_capital = resolve_total_capital(context=context)
    assert total_capital == 0.0
    assert total_capital <= 0  # G5 triggers: passed=False, severity='critical'


def test_g5_contract_explicit_negative_capital_fails_closed():
    """Resolver with explicit capital < 0 returns negative, allowing G5 check total_capital <= 0 to fail closed."""
    context = {"total_capital": -1000.0, "daily_pnl": 0.0}
    total_capital = resolve_total_capital(context=context)
    assert total_capital == -1000.0
@pytest.mark.asyncio
async def test_g5_live_gate_execution_missing_context_uses_default():
    """G5 live check with missing capital context uses 500,000 and passes when loss < 15,000."""
    from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
    gate = G5MaxDailyLoss(config={"max_daily_loss_pct": 3.0})
    res = await gate.check(signal=None, context={"daily_pnl": -5000.0})
    assert res.passed is True
    assert res.severity == "info"
    assert res.threshold == 15000.0


@pytest.mark.asyncio
async def test_g5_live_gate_execution_explicit_zero_fails_closed():
    """G5 live check with explicit capital=0.0 fails closed with critical severity."""
    from risk.gates.g5_max_daily_loss import G5MaxDailyLoss
    gate = G5MaxDailyLoss(config={"max_daily_loss_pct": 3.0})
    res = await gate.check(signal=None, context={"total_capital": 0.0, "daily_pnl": 0.0})
    assert res.passed is False
    assert res.severity == "critical"
    assert "Total capital is zero" in res.message


@pytest.mark.asyncio
async def test_g3_live_gate_execution_uses_canonical_default():
    """G3 live check with missing capital context uses 500,000 (25% = 125,000 max allowed)."""
    from risk.gates.g3_max_position_size import G3MaxPositionSize
    gate = G3MaxPositionSize(config={"max_per_position_pct": 25.0})
    signal = MagicMock(entry_price=1000.0, quantity=100)  # 100,000 value <= 125,000
    res = await gate.check(signal=signal, context={})
    assert res.passed is True
    assert res.threshold == 125000.0


def test_no_circular_import_fresh():
    """Ensure core.capital_resolver, config.settings, and api.dependencies can be imported without cycles.

    v0.4.2: this test used to pop the modules from sys.modules and NEVER put
    them back — leaving a SECOND Settings class + singleton alive for the
    rest of the session. Any later test that monkeypatched
    ``Settings.save`` via a fresh ``from config.settings import Settings``
    patched the NEW class while the routes (imported earlier) still held the
    OLD instance with the REAL save() — silently re-serializing the shipped
    defaults.yaml with test payloads. The original modules are now restored
    in a finally block so exactly ONE Settings class exists for the session.
    """
    import sys
    mods = ["core.capital_resolver", "config.settings", "api.dependencies"]
    # Snapshot the ORIGINAL modules so the import-cycle check can never
    # leave duplicate module copies in sys.modules.
    originals = {m: sys.modules.get(m) for m in mods}
    try:
        # Clear from cache to verify fresh import order
        for mod in mods:
            sys.modules.pop(mod, None)

        import core.capital_resolver
        import config.settings
        import api.dependencies

        assert hasattr(core.capital_resolver, "resolve_total_capital")
    finally:
        # Restore the original module objects — the freshly imported copies
        # (and their extra Settings singleton) are dropped, so every later
        # import in the session resolves back to the one true module.
        for mod, original in originals.items():
            if original is not None:
                sys.modules[mod] = original
            else:
                sys.modules.pop(mod, None)
