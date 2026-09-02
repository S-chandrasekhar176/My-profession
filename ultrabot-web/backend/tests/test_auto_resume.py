"""Regression tests for HOTFIX #8 — crash-aware engine auto-resume.

Drill evidence (2026-09-01 13:06 IST): killing the backend process leaves the
same-day session in status="running"; on reboot the engine previously sat in
"stopped" with stop-losses unenforced until a human started it. The
auto-resume helper restarts the engine ONLY for crashed (still-"running")
same-day sessions and preserves user intent otherwise.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.auto_resume import auto_resume_if_crashed


def _engine_with_session(sess: dict | None):
    eng = SimpleNamespace()
    eng.session_manager = SimpleNamespace(
        get_same_day_session=AsyncMock(return_value=sess)
    )
    eng.start = AsyncMock(return_value={"status": "started"})
    return eng


@pytest.mark.asyncio
async def test_resumes_crashed_running_session():
    """status='running' (crash signature) -> engine.start with saved mode/broker."""
    eng = _engine_with_session({
        "session_id": "s-123",
        "status": "running",
        "mode": "paper",
        "broker": "fyers",
    })
    resumed = await auto_resume_if_crashed(eng, settle_delay=0)
    assert resumed == "s-123"
    eng.start.assert_awaited_once_with(mode="paper", broker_name="fyers")


@pytest.mark.asyncio
async def test_never_resumes_explicitly_stopped_session():
    """status='stopped' (graceful stop) -> user intent respected, no start."""
    eng = _engine_with_session({
        "session_id": "s-456",
        "status": "stopped",
        "mode": "paper",
        "broker": "fyers",
    })
    resumed = await auto_resume_if_crashed(eng, settle_delay=0)
    assert resumed is None
    eng.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_never_resumes_completed_session():
    eng = _engine_with_session({
        "session_id": "s-789",
        "status": "completed",
        "mode": "paper",
        "broker": "fyers",
    })
    assert await auto_resume_if_crashed(eng, settle_delay=0) is None
    eng.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_auto_resume_when_mode_broker_unknown():
    """Crashed session with unknown mode/broker must NOT blind-start."""
    eng = _engine_with_session({
        "session_id": "s-001",
        "status": "running",
        "mode": "unknown",
        "broker": "unknown",
    })
    assert await auto_resume_if_crashed(eng, settle_delay=0) is None
    eng.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_lookup_failure_never_raises():
    """DB errors during lookup must be swallowed (startup must stay clean)."""
    eng = SimpleNamespace()
    eng.session_manager = SimpleNamespace(
        get_same_day_session=AsyncMock(side_effect=RuntimeError("db down"))
    )
    eng.start = AsyncMock()
    assert await auto_resume_if_crashed(eng, settle_delay=0) is None
    eng.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_same_day_session_is_noop():
    eng = _engine_with_session(None)
    assert await auto_resume_if_crashed(eng, settle_delay=0) is None
    eng.start.assert_not_awaited()
