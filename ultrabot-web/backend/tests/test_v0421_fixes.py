"""v0.4.21 regression tests — Phase A (verify & stabilize).

Covers the wed_v0.4.21 fix wave:
  1. loop_health.compute_loop_stalled_seconds — the None-beat hole: a
     running-like engine whose main-loop task never beat (the Sep-9
     "post-restart dead loop, UI said scanning for 2h" failure) must now be
     reported with the explicit NEVER_BEAT_SENTINEL (-1.0), not None.
  2. UltraBotEngine._on_main_task_done — loop supervision: a main-loop task
     that dies (exception / cancellation / surprise-return) while the state
     still claims running-like must flip the state to ERROR.
  3. should_stall_restart — pure watchdog escalation policy: threshold,
     daily cap, market-open gate, intentional-stop marker, shared
     anti-storm restart guard.
  4. market_open_ist — NSE regular session boundary logic.
  5. /api/health contract — loop_never_beat boolean mirrors the sentinel.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from core.engine_state import EngineState  # noqa: E402
from core.loop_health import (  # noqa: E402
    NEVER_BEAT_SENTINEL,
    compute_loop_stalled_seconds,
    market_open_ist,
    should_stall_restart,
)

IST = timezone(timedelta(hours=5, minutes=30))


# ────────────────────────────────────────────────
# 1. compute_loop_stalled_seconds (None-beat hole)
# ────────────────────────────────────────────────

def test_running_with_no_beat_reports_never_beat_sentinel():
    """THE Sep-9 hole: state=running, beat=None must NOT read as healthy None."""
    result = compute_loop_stalled_seconds(None, "running")
    assert result == NEVER_BEAT_SENTINEL
    assert result < 0  # unambiguous: a real stall age is never negative


def test_running_with_no_beat_for_every_running_like_state():
    for state in ("running", "paused", "scanning"):
        assert compute_loop_stalled_seconds(None, state) == NEVER_BEAT_SENTINEL


def test_stopped_engine_reports_none_not_applicable():
    assert compute_loop_stalled_seconds(None, "stopped") is None
    assert compute_loop_stalled_seconds(None, "error") is None
    beat = datetime.now(IST) - timedelta(seconds=30)
    assert compute_loop_stalled_seconds(beat, "stopped") is None


def test_running_with_stale_beat_reports_age():
    beat = datetime.now(IST) - timedelta(seconds=735.4)
    age = compute_loop_stalled_seconds(beat, "running")
    assert 730.0 <= age <= 740.0


def test_engine_state_enum_accepted():
    """Callers pass EngineState enums, not strings."""
    age = compute_loop_stalled_seconds(None, EngineState.RUNNING)
    assert age == NEVER_BEAT_SENTINEL
    assert compute_loop_stalled_seconds(None, EngineState.STOPPED) is None


def test_malformed_beat_string_falls_to_sentinel_when_running():
    assert compute_loop_stalled_seconds("not-a-date", "running") == NEVER_BEAT_SENTINEL


def test_iso_string_beat_is_parsed():
    beat = datetime.now(IST) - timedelta(seconds=15)
    age = compute_loop_stalled_seconds(beat.isoformat(), "running")
    assert 10.0 <= age <= 25.0


# ────────────────────────────────────────────────
# 2. Loop supervision callback
# ────────────────────────────────────────────────

def _finish_task(coro_factory) -> asyncio.Task:
    """Run a coroutine to completion on a fresh loop and return its task."""

    async def _runner():
        task = asyncio.create_task(coro_factory())
        await asyncio.sleep(0.01)
        return task

    return asyncio.run(_runner())


def _fake_engine(state):
    return SimpleNamespace(state=state)


def test_supervision_flips_error_when_task_raises_while_running():
    from core.engine import UltraBotEngine

    async def boom():
        raise RuntimeError("loop died pre-beat")

    task = _finish_task(boom)
    fake = _fake_engine(EngineState.RUNNING)
    UltraBotEngine._on_main_task_done(fake, task)
    assert fake.state == EngineState.ERROR


def test_supervision_flips_error_on_cancelled_task_while_running():
    from core.engine import UltraBotEngine

    async def hang():
        await asyncio.sleep(60)

    async def _runner():
        task = asyncio.create_task(hang())
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        return task

    task = asyncio.run(_runner())
    assert task.cancelled()
    fake = _fake_engine(EngineState.SCANNING)
    UltraBotEngine._on_main_task_done(fake, task)
    assert fake.state == EngineState.ERROR


def test_supervision_flips_error_on_surprise_return_while_running():
    from core.engine import UltraBotEngine

    async def fine():
        return "done"

    task = _finish_task(fine)
    fake = _fake_engine(EngineState.PAUSED)
    UltraBotEngine._on_main_task_done(fake, task)
    assert fake.state == EngineState.ERROR


def test_supervision_respects_truthful_states():
    """Graceful stop() or the max-retries ERROR exit must NOT be overridden."""
    from core.engine import UltraBotEngine

    async def fine():
        return "done"

    task = _finish_task(fine)
    for truthful in (EngineState.STOPPED, EngineState.ERROR, EngineState.STARTING):
        fake = _fake_engine(truthful)
        UltraBotEngine._on_main_task_done(fake, task)
        assert fake.state == truthful, f"state {truthful} must stay untouched"


def test_supervision_callback_never_raises():
    from core.engine import UltraBotEngine

    async def fine():
        return "done"

    task = _finish_task(fine)
    hostile = SimpleNamespace(state=None)  # no .value, missing attrs
    # must not raise even against a broken engine shell; a non-running-like
    # state is left untouched by design (the "already truthful" early return)
    UltraBotEngine._on_main_task_done(hostile, task)
    assert hostile.state is None


# ────────────────────────────────────────────────
# 3. should_stall_restart policy
# ────────────────────────────────────────────────

def _ok(stalled=1000.0, restarts=0, market=True, marker=False, since=None):
    return dict(
        stalled_seconds=stalled,
        stall_restarts_today=restarts,
        market_open=market,
        stop_marker_fresh=marker,
        seconds_since_last_restart=since,
    )


def test_stall_restart_happy_path():
    decision, _ = should_stall_restart(**_ok())
    assert decision is True


def test_stall_restart_below_threshold_vetoed():
    decision, reason = should_stall_restart(**_ok(stalled=600.0))
    assert decision is False
    assert "threshold" in reason


def test_stall_restart_never_beat_escalates():
    decision, _ = should_stall_restart(**_ok(stalled=NEVER_BEAT_SENTINEL))
    assert decision is True


def test_stall_restart_daily_cap_vetoed():
    decision, reason = should_stall_restart(**_ok(restarts=2))
    assert decision is False
    assert "cap" in reason


def test_stall_restart_market_closed_vetoed():
    decision, reason = should_stall_restart(**_ok(market=False))
    assert decision is False
    assert "market" in reason


def test_stall_restart_stop_marker_vetoed():
    decision, reason = should_stall_restart(**_ok(marker=True))
    assert decision is False
    assert "marker" in reason


def test_stall_restart_recent_restart_guard_vetoed():
    decision, reason = should_stall_restart(**_ok(since=60.0))
    assert decision is False
    assert "guard" in reason


def test_stall_restart_guard_expires():
    decision, _ = should_stall_restart(**_ok(since=300.0))
    assert decision is True


def test_stall_restart_no_stall_data_vetoed():
    decision, _ = should_stall_restart(**_ok(stalled=None))
    assert decision is False


# ────────────────────────────────────────────────
# 4. market_open_ist boundaries
# ────────────────────────────────────────────────

def _ist(y, m, d, hh, mm, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


def test_market_open_bounds_inclusive():
    assert market_open_ist(_ist(2026, 9, 9, 9, 14, 59)) is False   # 1s before
    assert market_open_ist(_ist(2026, 9, 9, 9, 15, 0)) is True     # open
    assert market_open_ist(_ist(2026, 9, 9, 12, 0)) is True        # midday
    assert market_open_ist(_ist(2026, 9, 9, 15, 30, 0)) is True    # close (incl.)
    assert market_open_ist(_ist(2026, 9, 9, 15, 31)) is False      # after


def test_market_open_weekends_closed():
    # 2026-09-05 is a Saturday, 2026-09-06 a Sunday
    assert market_open_ist(_ist(2026, 9, 5, 11, 0)) is False
    assert market_open_ist(_ist(2026, 9, 6, 11, 0)) is False


def test_market_open_converts_other_timezones():
    # 04:15 UTC == 09:45 IST on a Wednesday (2026-09-09) → open
    utc = timezone.utc
    assert market_open_ist(datetime(2026, 9, 9, 4, 15, tzinfo=utc)) is True
    # 10:45 UTC == 16:15 IST → closed
    assert market_open_ist(datetime(2026, 9, 9, 10, 45, tzinfo=utc)) is False


# ────────────────────────────────────────────────
# 5. /api/health contract shape
# ────────────────────────────────────────────────

def test_health_contract_never_beat_flag_logic():
    """Mirror of the app.py block: beat=None + running-like → flag + sentinel."""
    beat = None
    state_val = "running"
    stalled = compute_loop_stalled_seconds(beat, state_val)
    never = bool(beat is None and state_val in ("running", "paused", "scanning"))
    assert stalled == NEVER_BEAT_SENTINEL and never is True
    # healthy engine: beat fresh, running → numeric, flag False
    beat = datetime.now(IST) - timedelta(seconds=5)
    stalled = compute_loop_stalled_seconds(beat, state_val)
    never = bool(beat is None and state_val in ("running", "paused", "scanning"))
    assert stalled >= 0 and never is False
