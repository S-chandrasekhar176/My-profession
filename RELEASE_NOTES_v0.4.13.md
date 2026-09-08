# v0.4.13 — M2 Hardening: persistence, rehydration, alert & sizing correctness

Milestone-2 hardening release driven entirely by live-session evidence from
2026-09-07 (first full v0.4.12 paper-trading day, 6 trades, net −₹28.78, and
a midday platform workspace recycle that destroyed the day's DB).

## 1. Periodic DB backups (in-process, observable)

`core/db_backup.py` — an in-process asyncio job wired in `app.py` lifespan:

- Snapshot every 15 min (configurable) via the **SQLite online backup API** —
  consistent copy while the engine keeps writing (WAL-safe, no engine pause).
- Snapshots → `persist/snapshots/` (outside the repo; this directory
  verifiably survives platform workspace recycles). Boot snapshot included.
- **EOD copy at 15:35 IST** with a stable filename
  (`ultrabot_eod_YYYYMMDD.db`) — the handoff/EOD artifact.
- Retention: newest 32 kept, older pruned.
- Never raises into the app (same policy as the Telegram loops); every cycle
  logged, so a silent death like the Sep-4 external cron cannot recur.
- Config: new `persistence:` section in `defaults.yaml`
  (`enabled / snapshot_interval_minutes / keep_snapshots / eod_time /
  snapshot_dir / eod_dir`).

## 2. Handoff exporter (sanitized DB for the midday split-run protocol)

`core/handoff.py` + `scripts/export_handoff.py` CLI:

- Produces `download/ultrabot_handoff_YYYYMMDD-HHMM.db` — a consistent copy
  carrying the full day's data (trades, positions, signals,
  shadow_outcomes, sessions) with **`broker_credentials` DELETED**.
- Rationale: the repo is public and `.encryption_key` sits next to the DB —
  raw DB must never travel (git or otherwise). Receiving side re-auths the
  broker normally; ledger + ML samples continue from Part 1.
- `--verify` mode (integrity check + zero-secret-rows assertion) runs
  automatically after every export.

## 3. Engine rehydration fix (the 09:42 bug)

Live evidence (09:41:32→09:42:52 IST): a same-day restart left the paper
broker's in-memory book empty while the DB held 3 open positions; the next
`save_state()` then persisted **0 positions**, destroying the session
snapshot.

- `PaperBroker.rehydrate_positions(rows)`: seeds the book from the DB
  ledger (source of truth) and mirrors the original BUY/SELL leg's capital
  movement (`capital ∓ invested + fees`) so margin/available math is
  identical to a no-restart session. Existing-open symbols are skipped;
  malformed rows are skipped with warnings.
- `engine.start()` same-day path: calls the above after state recovery
  (paper mode only — live brokers keep positions server-side).
- `SessionManager.save_state()` **DB-truth fallback**: merges DB open
  positions missing from the broker book, so a state snapshot can never
  "forget" live positions even if an edge slips through.

## 4. Counter restore on same-day resume

Live evidence: `/status` showed `Trades 0` with 3 DB trades after the
09:42 restart. On same-day resume the engine now restores
`_trades_executed` from the DB trade ledger (scan/signal counters have no
per-event ledger and honestly restart at zero).

## 5. Canary false-positive fix

Live evidence: 10:50 & 11:58 IST — canary fired "engine is scanning"
(briefly "blind") while the engine was actively scanning. New
`_CANARY_HEALTHY_STATES = (running, starting, scanning, paused)`:
- `scanning` is the transient state DURING a scan tick (the main loop runs
  while RUNNING/PAUSED/SCANNING and still manages all positions);
- `paused` is user-intentional no-new-entries — SLs/targets/time-stops are
  still enforced, so it is not blind (alerting on it every 45 min = noise).
`stopped`/`error`/unknown states still alert.

## 6. G20_Sizing pre-check (no more approve-then-reject)

Live evidence: 11:26 IST — BOSCHLTD (₹47.9k vs per-trade capital) emitted an
opportunity card sized 0 qty; the user tapped Approve and the confirm path
rejected with "Position size calculated as 0". The scan loop now blocks a
zero/negative sized quantity **before** the card exists, with the same
truthful bookkeeping as the G17 re-check (counters, telemetry event,
`G20_Sizing` gate attribution) and registers a `gate_blocked` shadow sample
(consistent with G1) so the ML ledger sees the block. The confirm-path
rejection remains as the last-line defense.

## 7. Feed watchdog (verified, unchanged)

Audit confirmed the existing failover design: 3 consecutive primary
failures → `switch_to_backup`; `switch_to_primary` recovery path exists;
tests cover DEGRADED→DOWN escalation, recovery alerts and frozen-feed
detection (consistent with the self-recovered 10:25 IST feed-drop window).

## Tests

26 new tests (`test_v0413_backup_handoff.py`, `test_v0413_rehydration.py`,
`test_v0413_canary_sizing.py`). Full suite: **940 passed / 0 failed /
0 skipped** (914 baseline + 26). Pristine-config snapshot refreshed for the
intentional `persistence:` addition (per the drift-guard's own procedure).

## Config migration

None required. Defaults are on; sandbox installs that want the EOD copy in
a different location set `persistence.eod_dir` in `defaults.local.yaml`.
