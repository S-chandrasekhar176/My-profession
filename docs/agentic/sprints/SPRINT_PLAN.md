# SPRINT PLAN — P1-pilot-wave-W1 (SM output)

- Phase: P1 (M2.5 Stabilize & Accumulate) | Wave goal: one-pass `--check` health
  probe in `scripts/session_watchdog.py` to serve the 09:00 pre-open smoke —
  run all checks once, print status, exit 0/1, no restart loop, no side effects.
- Base: origin/main@277a80254e260dc04fc65767bec6549d4f25fa1b (PR #13 v0.4.17 +
  PR #14 harness v1 merged; suite baseline 962/0/0, see
  `RELEASE_NOTES_v0.4.17.md:94`) | Planned duration: 1 evening (D-008 cadence).

## Tasks (each becomes one DEV branch + PR)
| # | Task | Acceptance criteria | Estimate |
|---|---|---|---|
| 1 | Add `--check` one-pass mode to `scripts/session_watchdog.py` (argparse; loop stays default path) | 1. `python scripts/session_watchdog.py --check` performs the checks ONCE and exits — never enters the poll loop (`while True` at line 351 unreachable in this mode). 2. Exit code 0 iff backend health OK (`health_ok()` → 200, scripts/session_watchdog.py:95) AND `persist/heartbeat.log` last `UP` entry age ≤ 60 s (4× poll interval); any other result exits 1. 3. Printed status lines (one per check, human-readable, no secrets): backend health + latency; heartbeat age; canary ages `a`/`b` vs `CANARY_STALE_SECONDS=90` (line 64); intentional-stop marker freshness (`stop_marker_fresh()`, line 286); restarts today (`restarts_today()`, line 267) vs `MAX_RESTARTS_PER_DAY=5`. 4. STRICTLY READ-ONLY: in `--check` mode `heartbeat()`, `spawn_canaries()`, `start_backend()`, `telegram_alert()`, `write_death_report()`, `bump_restart_counter()`, `snapshot()` are never called — test proves zero writes under `--check` (heartbeat.log mtime/size unchanged; no canary heartbeat files created; no death_reports dir writes). 5. Regression-first: tests fail on `origin/main` (no `--check` flag today — `main()` runs the loop unconditionally, lines 342/420) and pass on branch. 6. `bash scripts/run_harness.sh pr` green (962+0 new ≥ 962/0/0) + CI green (D-007). Gate served: P1 watchdog/pre-open-smoke reliability row. | S |
| 2 | Tests for `--check` (only file touched besides the script: `ultrabot-web/backend/tests/test_v0418_watchdog_check.py`, matching `test_v0417_restart_429.py` naming) | Covers: healthy backend + fresh heartbeat → exit 0; backend down → exit 1; stale heartbeat → exit 1; canary ages + marker + restart-count printed as informational and NOT affecting exit code (unless ARCH changes rule below); read-only guarantee (no side-effect files); loop never entered. All paths mocked/temp-dir based — no network, no live backend, no real `persist/`. | S |

## Debate record
- ARCH verdict: <PENDING — not yet obtained>
- Changes applied to plan: <none yet>
- Rounds used: 0/2 (max 2, then USER decides)
- Open questions for ARCH:
  1. `_proc_census()` (line 109) reads Linux `/proc` — on USER's local Windows
     runtime (D-006) this silently returns []. Should W1 add a cross-platform
     census (psutil or tasklist) inside the same script, or keep W1 minimal and
     accept informational-only census on Windows? (Scope cap says "that script
     + tests only" — both options stay inside it.)
  2. Heartbeat-staleness threshold 60 s: correct for a 09:00 smoke where the
     watchdog daemon may legitimately not be started yet (check would fail →
     operator alert before open — desired?), or should heartbeat freshness be
     WARN-only in W1?
  3. Should intentional-stop-marker freshness ever flip the exit code to 0
     while backend is down? SM proposes NO for a pre-open smoke (backend must
     be up for open); ARCH to confirm.

## Out of scope (explicitly)
- Any change to the watchdog loop behavior, restart policy, backoff, cap, or
  Telegram alert wording (watchdog v2 is merged and soaked — do not touch).
- Scheduled automation of the 09:00 smoke (cron/Task Scheduler) — W1 delivers
  the manual command only; scheduling is a later wave if W1 proves out.
- Machine-readable `--json` output, extra checks (DB reachability, feed
  config, Telegram creds presence) — future waves; nothing invented now.
- Portability refactor of hard-coded sandbox paths
  (`/home/z/my-project/...`, lines 44–55) beyond what --check strictly needs.
- No changes to `scripts/canary_heartbeat.py`, `daemonize.py`, or backend code.

## Done definition for this wave
- [ ] All task acceptance criteria met with evidence
- [ ] Harness `pr` battery green + CI green
- [ ] qa_code_report.md PASS | [ ] ROADMAP_STATE.md updated (evidence links)