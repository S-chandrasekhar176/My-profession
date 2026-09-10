"""Loop-health primitives shared by the engine, the health endpoint and the
out-of-process session watchdog.

v0.4.21 — closes the two holes the Sep-9 13:35 incident exposed:

  HOLE 1 (None-beat): ``loop_stalled_seconds`` used to be reported as ``None``
  whenever ``_loop_last_beat`` was ``None``. A "running" engine whose main-loop
  task died BEFORE its first beat therefore looked perfectly healthy forever —
  exactly the invisible post-restart failure of Sep-9 (UI said "scanning" for
  two hours while nothing scanned). Now a running-like engine with no beat is
  reported with the explicit ``NEVER_BEAT_SENTINEL`` (-1.0) plus a boolean
  ``loop_never_beat`` so the UI and the watchdog can distinguish
  "not applicable" from "running but the loop never started".

  HOLE 2 (alert-only): the watchdog alerted on a stalled loop but never
  recovered it. A wedged loop during market hours leaves positions unmanaged —
  strictly worse than a restart with the (proven) rehydration path. The
  decision policy lives here as a PURE function so the backend test-suite can
  exercise every rail without touching the live watchdog.

Zero third-party imports: the watchdog runs on the host interpreter and
imports this module via sys.path — it must not pull fastapi/sqlalchemy.
"""
from datetime import datetime, timedelta, timezone

# States in which the engine is SUPPOSED to be iterating its main loop.
RUNNING_STATES = ("running", "paused", "scanning")

# Reported when state is running-like but the loop has never completed an
# iteration (beat is None). Negative by design: a real stall age can never be
# negative, so downstream consumers can branch on it unambiguously.
NEVER_BEAT_SENTINEL = -1.0

IST = timezone(timedelta(hours=5, minutes=30))

# NSE equity regular session, in IST.
MARKET_OPEN_TIME = (9, 15)
MARKET_CLOSE_TIME = (15, 30)

# ── Stall auto-restart policy (watchdog side) ──────────────────────────────
STALL_RESTART_THRESHOLD_SECONDS = 900.0   # 15 min of a dead loop
STALL_RESTART_MAX_PER_DAY = 2             # separate from death-restart cap
STALL_RESTART_GUARD_SECONDS = 180.0       # no restart within 3 min of any restart


def compute_loop_stalled_seconds(beat, state, now=None):
    """Truthful loop-stall age for a given beat/state pair.

    Returns:
      None                — engine not in a running-like state (not applicable)
      NEVER_BEAT_SENTINEL — running-like but the loop never beat (HOLE 1)
      float >= 0          — seconds since the last completed loop iteration
    """
    if isinstance(state, str):
        state_val = state.lower()
    else:
        state_val = str(getattr(state, "value", state) or "").lower()
    if state_val not in RUNNING_STATES:
        return None
    if beat is None:
        return NEVER_BEAT_SENTINEL
    if isinstance(beat, str):
        try:
            beat = datetime.fromisoformat(beat)
        except (TypeError, ValueError):
            return NEVER_BEAT_SENTINEL
    now = now or datetime.now(beat.tzinfo or IST)
    try:
        age = (now - beat).total_seconds()
    except TypeError:
        return NEVER_BEAT_SENTINEL
    return round(max(age, 0.0), 1)


def market_open_ist(now=None):
    """True inside the NSE regular session (Mon–Fri 09:15–15:30 IST, inclusive
    bounds). Weekends are always closed. Pure — no network, no config."""
    now = now or datetime.now(IST)
    local = now.astimezone(IST)
    if local.weekday() >= 5:  # Sat=5, Sun=6
        return False
    minutes = local.hour * 60 + local.minute
    open_min = MARKET_OPEN_TIME[0] * 60 + MARKET_OPEN_TIME[1]
    close_min = MARKET_CLOSE_TIME[0] * 60 + MARKET_CLOSE_TIME[1]
    return open_min <= minutes <= close_min


def should_stall_restart(
    stalled_seconds,
    stall_restarts_today,
    market_open,
    stop_marker_fresh,
    seconds_since_last_restart,
    threshold=STALL_RESTART_THRESHOLD_SECONDS,
    max_per_day=STALL_RESTART_MAX_PER_DAY,
    guard_seconds=STALL_RESTART_GUARD_SECONDS,
):
    """Pure decision: escalate a stalled loop to an auto-restart?

    Rails (each alone vetoes the restart):
      threshold   — stall must have reached the sustained threshold
      daily cap   — at most max_per_day stall-restarts per day
      market gate — only restart while the NSE session is open
      stop marker — an intentional-stop marker is honored
      restart guard — never restart within guard_seconds of ANY restart
                      (death-restarts included) so the two paths cannot storm

    Returns (decision: bool, reason: str) — reason is for the watchdog log.
    """
    if stalled_seconds is None:
        return False, "no stall data"
    if stalled_seconds >= 0 and stalled_seconds < threshold:
        return False, f"stall {stalled_seconds:.0f}s < threshold {threshold:.0f}s"
    if stall_restarts_today >= max_per_day:
        return False, f"stall-restart cap reached ({stall_restarts_today}/{max_per_day})"
    if not market_open:
        return False, "market closed — restart deferred"
    if stop_marker_fresh:
        return False, "intentional-stop marker fresh"
    if seconds_since_last_restart is not None and seconds_since_last_restart < guard_seconds:
        return False, f"restart guard active ({seconds_since_last_restart:.0f}s < {guard_seconds:.0f}s)"
    return True, f"stall {stalled_seconds:.0f}s sustained — recovering loop"
