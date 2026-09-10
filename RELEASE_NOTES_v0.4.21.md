# v0.4.21 — Phase A: Verify & Stabilize

**Branch:** `wed_v0.4.21` (from merged main `877bfb7` = PR #17 / v0.4.20)
**Theme:** close out the Sep-9 13:35 incident, make the loop-stall failure mode impossible to miss AND self-recovering, add PR quality gates (CI).

---

## 1. Root cause closed: the invisible Sep-9 13:35 failure

**What the evidence says.** The gold-mine DB (`upload/ultrabot.db`, accessed strictly read-only) holds two sessions (Sep 8 from 12:56 IST, Sep 9 from 09:09 IST) and no `death_reports` table — the restart forensics therefore leaned on the codebase itself. `scripts/session_watchdog.py` carries the incident record: *"the invisible Sep-9 13:35 failure: UI said 'scanning' for 2 hours while nothing scanned"*. Process alive, `/api/health` green, main loop wedged. DB corroboration: Sep 9 shows zero signal rows 13:00–15:30 and the last trade exit at 12:25:57 — the loop went silent right after midday.

**Two holes found in the v0.4.18 detection net** (detection existed, but could not see this exact shape and could not recover from it):

| # | Hole | Effect |
|---|------|--------|
| H1 | `loop_stalled_seconds` returned `None` when `_loop_last_beat is None` | A loop that died **before its first beat** (the post-restart case) read as *healthy forever* |
| H2 | `asyncio.create_task(self._main_loop())` had **no done-callback** | A loop task that raised / was cancelled silently left `state=running` — UI kept saying "scanning" |
| H3 | Watchdog on stall: **alert only, never restart** | Wedged loop during market hours = positions unmanaged for as long as it takes a human to notice |

## 2. Fixes shipped

### Fix 1 — `core/loop_health.py` (new, zero-dependency)
Single source of truth for loop-health semantics, imported by the engine, `/api/health`, **and** the out-of-process watchdog (so all three can never disagree):
- `compute_loop_stalled_seconds(beat, state)` → `None` (not running) / `-1.0` sentinel (**running but never beat**) / stall age in seconds.
- `market_open_ist()` — NSE regular session boundaries (Mon–Fri 09:15–15:30 IST, inclusive).
- `should_stall_restart(...)` — pure escalation policy with five rails.

### Fix 2 — truthful status at both endpoints
`/api/engine/status` (engine.py) and `/api/health` (app.py) now report:
- `loop_stalled_seconds: -1.0` + `loop_never_beat: true` for the running-but-never-beat case (H1 closed),
- numeric stall age otherwise, unchanged for healthy engines.

### Fix 3 — loop supervision (H2 closed)
`engine.start()` attaches `_on_main_task_done` as a done-callback on the main-loop task. If the task dies (unhandled exception, cancellation outside `stop()`, surprise return) **while the state still claims running-like**, the state flips to `ERROR` and a critical log records why. Graceful stops and the max-retries ERROR path are respected (no state lying in the other direction either).

### Fix 4 — watchdog v3: stall auto-restart (H3 closed)
`session_watchdog.py` escalates a sustained stall (≥900s, two consecutive health polls, never-beat counts) into a **STALL-RESTART** with Telegram alert. Rails, each an independent veto:
1. 15-minute sustained threshold (no reaction to brief hiccups),
2. max **2 stall-restarts/day** (separate counter from death-restarts),
3. **market-open gate** — restarts only inside 09:15–15:30 IST,
4. intentional-stop marker honored,
5. **shared 180s anti-storm guard** — a stall-restart and a death-restart can never race each other.

Position rehydration (proven since v0.4.13) makes a restart strictly better than a wedged loop with open positions.

### Fix 5 — CI quality gates (`.github/workflows/ci.yml`, new)
- `backend-tests`: Python 3.12, full pytest suite on every PR and on main.
- `frontend-typecheck`: `bun install --frozen-lockfile` + `bunx tsc --noEmit`.
- Concurrency-grouped, read-only permissions, pip-cached.

## 3. Verification

- New tests: `tests/test_v0421_fixes.py` — **25 tests** (sentinel semantics ×7, supervision callback ×5, restart policy ×9, market-hours boundaries ×3, health contract ×1).
- Full backend suite: **1009 passed / 0 failed / 0 skipped** (35.1s) — up from 984.
- `bunx tsc --noEmit`: clean.
- v0.4.20 markers re-verified in merged main (invalidated-route DB merge, `close_qty` semantics, backtest honesty, WS expired marking).
- Gold-mine DB touched **read-only** (URI `?mode=ro`); no writes, no migrations, no wipes.

## 4. Operator notes

1. Deploy at a flat window (never 09:00–15:35 IST) — restart + watchdog + backend all pick up the new modules together.
2. On next mid-session wedge expect: Telegram **STALLED** alert at 5 min, **STALL-RESTART** at 15 min, UI state `error` if the loop task actually died.
3. Watchdog state files: `watchdog_stall_restarts.json` (new) joins `watchdog_restarts.json`.
