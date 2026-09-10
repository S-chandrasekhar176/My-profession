# v0.4.21 — Phase A: Verify & Stabilize

**Branch:** `wed_v0.4.21` (from merged main `877bfb7` = PR #17 / v0.4.20)
**Theme:** close out the Sep-9 13:35 incident, make the loop-stall failure mode impossible to miss AND self-recovering, add PR quality gates (CI), plus the wave-2 fixes: DB connection-leak hardening + Telegram deaf-bot self-healing.

---

## 0. Wave 2 — connection leaks + Telegram silence (user report, Sep 10)

**Reported:** repeated `SAWarning / NullPool ERROR: garbage collector is trying to clean up non-checked-in connection (Thread-4349/4350)` and **"Bot stopped responding to Telegram messages since 11:16am"**.

### Root causes

| # | Finding | Mechanism |
|---|---------|-----------|
| W1 | **Connection-GC leak window** | Every production session site closes properly on exception paths — BUT `Repository.close()` was a plain `await self.session.close()` inside consumers' `finally` blocks. When the enclosing task is cancelled **during** the close (handler timeouts, `stop()` teardown, loop wedges like 11:16), the close itself is interrupted → the aiosqlite connection is never returned → GC drops it later (the SAWarning), **leaking its dedicated daemon thread** (the `Thread-4350` counter). |
| W2 | **Telegram poll loop — one hung handler freezes everything** | `poll_loop` awaited each `/command` handler **inline** with no timeout. A single hung handler (DB/HTTP wedge at 11:16) blocked the receive loop forever → bot deaf to ALL messages. |
| W3 | **Telegram loop tasks — fire-and-forget, no supervision, no heartbeat** | `asyncio.create_task(poll_loop())` with no done-callback: a dead poll task = silently dead bot, invisible from `/api/health` and the watchdog. Same disease as the Sep-9 engine loop. |

### Fixes

| Fix | What |
|-----|------|
| **F6** `Repository.close()` hardened (db/repository.py) | **Idempotent** (session ref dropped before the await — double-close can never double-spawn) + **shielded** (`asyncio.shield(session.close())` — a cancellation during close lets the inner close finish in the background; the connection always returns to the pool). `__aexit__` rollback hardened the same way. Every leak site in the codebase funnels through this one choke point. |
| **F7** Bounded command dispatch (telegram_interactive.py) | Every incoming update's handler now runs under `asyncio.wait_for(..., 30s)`. A hung handler is cancelled and logged; **the next message is still served**. |
| **F8** Telegram loop supervision + in-process respawn | `start()` tasks get done-callbacks: death while active → critical log + reason recorded + **auto-respawn up to 5/day per loop**. |
| **F9** Poll heartbeat on `/api/health` | New fields: `telegram_poll_alive`, `telegram_poll_stalled_seconds`, `telegram_poll_timeout`, `telegram_poll_respawns`, `telegram_poll_last_death`. |
| **F10** Watchdog: telegram-deaf alert | Edge-triggered Telegram alert when the poll heartbeat goes stale beyond `max(120s, poll_timeout+60s)` — the user gets pinged on the SAME channel the bot went deaf on. |

### Why the two symptoms are one incident
11:16 IST: a wedge/hang froze the poll loop (W2) — bot went silent. Sessions caught open mid-call during the freeze were later GC-dropped (W1) — the SAWarnings in the logs. Wave-2 fixes break both chains: hangs can no longer freeze polling (F7), dead loops respawn (F8), closes always complete (F6), and any residual deafness alerts to Telegram + `loop_stalled`-style visibility (F9/F10).

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

- New tests: `tests/test_v0421_fixes.py` — **30 tests** (sentinel semantics ×7, engine-loop supervision ×5, restart policy ×9, market-hours boundaries ×3, health contract ×1, Repository close ×2, telegram heartbeat/supervision/bounded-dispatch ×3).
- Full backend suite: **1014 passed / 0 failed / 0 skipped** (34.9s).
- `bunx tsc --noEmit`: clean.
- v0.4.20 markers re-verified in merged main (invalidated-route DB merge, `close_qty` semantics, backtest honesty, WS expired marking).
- Gold-mine DB touched **read-only** (URI `?mode=ro`); no writes, no migrations, no wipes.
- Connection-site audit: all `async_session_factory()` call sites + every `repo_getter` consumer (SessionManager, engine `_repo_context`, error_engine ×4, scheduler ×7, telegram ×3, G13 gate, fyers_candles) verified close-disciplined; the leak was the cancellation window inside `close()` itself, now shielded centrally.

## 4. Operator notes

1. Deploy at a flat window (never 09:00–15:35 IST) — restart + watchdog + backend all pick up the new modules together.
2. On next mid-session wedge expect: Telegram **STALLED** alert at 5 min, **STALL-RESTART** at 15 min, UI state `error` if the loop task actually died.
3. On a telegram-poll death: in-process respawn (up to 5/day) + **TELEGRAM POLL not responding** watchdog alert if staleness persists past the long-poll floor.
4. A hung `/command` now times out after 30s (logged, skipped) — subsequent messages keep flowing.
5. Watchdog state files: `watchdog_stall_restarts.json` (new) joins `watchdog_restarts.json`; new `/api/health` keys: `loop_never_beat`, `telegram_poll_*`.

## 5. Wave 3 (2026-09-10, post-deploy log forensics) — the actual leak trigger

User-supplied production logs pinned the GC storm to its source:

```
candles.py:288: RuntimeWarning: coroutine 'Repository.close' was never awaited
sqlalchemy.pool.impl.NullPool - ERROR - The garbage collector is trying to clean up
non-checked-in connection <AdaptedConnection <Connection(Thread-2884, ...)>> ...
brokers/fyers.py:332: SAWarning: ... (Thread-2884 ...)
```

**Root cause — `api/routes/candles.py::_get_fyers_quotes_broker()`:** the hand-rolled
session cleanup ran `asyncio.iscoroutine(res)` WITHOUT an in-scope `asyncio` import
(the module only imported asyncio inside a *different* function). The `NameError`
was silently eaten by `except Exception: pass`, so `await repo.close()` never ran.
Consequences, on **every `/api/live-quotes` poll** (header banner: ~3s per open tab):

1. `Repository.close` coroutine created but never awaited (RuntimeWarning),
2. `AsyncSession` never closed → with `NullPool` its aiosqlite connection dropped by
   the GC (SAWarning / NullPool-ERROR bursts at hot frames like `fyers.py:332`),
3. each dropped aiosqlite connection stranded its dedicated daemon thread —
   the Thread-28xx/43xx storm and mounting memory.

Wave 2's shielded, idempotent `Repository.close()` protected every *other* call
site but could not help here — the coroutine was never awaited at all.

**Fix:** session lifecycle now owned by context managers —
`async with async_session_factory() as session: async with Repository(session) as repo:`
— closing on success, error, AND cancellation paths; `import asyncio` added at
module scope as tripwire prevention.

**Tests:** new `tests/test_candles_quotes_broker_lifecycle.py` — 5 tests (success +
cached second poll, no-credentials path, credential-fetch raises, empty blob,
module-scope asyncio tripwire). Verified to **fail 5/5 on the pre-fix code** and
pass 5/5 on the fix.
