import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from risk.gates.g13_duplicate_signal import G13DuplicateSignal
from models.risk_state import GateResult


@pytest.mark.asyncio
async def test_g13_with_async_callable_repo_getter():
    """Verify G13 works with an async callable repo getter without NameError."""
    mock_signal = MagicMock()
    mock_signal.symbol = "RELIANCE"
    mock_signal.direction = "LONG"
    mock_signal.created_at = datetime.now().isoformat()

    mock_repo = AsyncMock()
    mock_repo.get_signals_by_symbol.return_value = [mock_signal]
    mock_repo.close = AsyncMock()

    async def repo_getter():
        return mock_repo

    gate = G13DuplicateSignal(config={"duplicate_signal_lookback_minutes": 15})
    gate.set_repository(repo_getter)

    incoming_signal = MagicMock()
    incoming_signal.symbol = "RELIANCE"
    incoming_signal.direction = "LONG"

    result = await gate.check(incoming_signal, context={})
    assert isinstance(result, GateResult)
    assert result.gate_name == "G13_DuplicateSignal"
    assert result.passed is False  # Duplicate found within 15 min
    assert "Duplicate LONG signal for RELIANCE" in result.message
    # Verify session close was called
    mock_repo.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_g13_with_direct_repository_object():
    """Verify G13 works when a direct resolved Repository object is injected."""
    mock_repo = AsyncMock()
    mock_repo.get_signals_by_symbol.return_value = []

    gate = G13DuplicateSignal(config={"duplicate_signal_lookback_minutes": 15})
    gate.set_repository(mock_repo)

    incoming_signal = MagicMock()
    incoming_signal.symbol = "INFY"
    incoming_signal.direction = "LONG"

    result = await gate.check(incoming_signal, context={})
    assert isinstance(result, GateResult)
    assert result.gate_name == "G13_DuplicateSignal"
    assert result.passed is True
    assert "No duplicate LONG signal for INFY" in result.message


@pytest.mark.asyncio
async def test_g13_allows_opposite_direction():
    """Verify G13 passes if recent signal was opposite direction."""
    recent_sig = MagicMock()
    recent_sig.symbol = "TCS"
    recent_sig.direction = "SHORT"
    recent_sig.created_at = datetime.now().isoformat()

    mock_repo = AsyncMock()
    mock_repo.get_signals_by_symbol.return_value = [recent_sig]

    gate = G13DuplicateSignal(config={"duplicate_signal_lookback_minutes": 15})
    gate.set_repository(mock_repo)

    incoming_signal = MagicMock()
    incoming_signal.symbol = "TCS"
    incoming_signal.direction = "LONG"

    result = await gate.check(incoming_signal, context={})
    assert result.passed is True
