# ROADMAP STATE — single source of truth for project position

> **Agents:** read this FIRST every session. **SM** owns this file: every status
> change MUST link evidence (PR number, test report path, or ticket path).
> Last updated: 2026-09-09 — PR #13 (v0.4.17) + PR #14 (harness v1) merged to
> main @ 277a802 by USER; sandbox re-verified 962/0/0 on merged main.

## Phase ladder (P0 → P9)

| Phase | Goal | Status | Exit gate |
|---|---|---|---|
| P0 | Core paper engine stable | **DONE** (v0.4.8→v0.4.17) | — |
| P1 | M2.5 stabilize: realtime reliability, restart resilience, watchdog | **IN PROGRESS** — day 1 of ~4 done (Sep 8); runtime host moving to USER's local machine | ≥100 realtime samples, 0 data-loss, 2 clean restart handoffs, 5+ clean sessions |
| P2 | F&O data foundation (option-chain poller, `greeks=1`, 2+ wk soak) | NOT STARTED — may run in parallel with P1 | soak complete, chain snapshot integrity verified |
| P3 | Model build (features → ML) | BLOCKED by P2 data | model beats baseline on replay |
| P4 | Shadow advisor (model suggests, engine ignores) | blocked by P3 | shadow parity ≥ target on live sessions |
| P5 | Veto mode (model can block trades, never place) | blocked by P4 | veto precision/recall agreed by USER |
| P6 | **USER GATE: live pilot, small size** | HUMAN-ONLY | USER signs off explicitly |
| P7 | **USER GATE: scale live pilot** | HUMAN-ONLY | USER signs off explicitly |
| P8 | F&O paper→live execution | blocked by P6/P7 | — |
| P9 | Scale + multi-strategy | future | — |

Live-pilot projection (approved in roadmap v1.0): mid-to-late October 2026.

## Version state

| Version | Content | State |
|---|---|---|
| v0.4.16 (PR #12) | PnL invariant fix, honest banner, chart live ticks, opportunity lifecycle generic fix, SHREECEM terminal-status invariant | MERGED to main @ 93efcef |
| v0.4.17 (PR #13) | pending-opportunity restart restore, Telegram 429 pacing + Retry-After, watchdog v2 + canaries (962 tests / 0 fail) | MERGED to main @ 277a802 |
| harness v1 (PR #14) | agentic harness scaffolding: HARNESS.md, 6 role charters, sprint/gate/QA templates, CI workflow (per D-002, D-007) | MERGED to main @ 277a802 |
| v0.4.18 (candidate) | silent-death instrumentation findings, any OPS tickets from first local sessions | not started |

## Known operational facts (do not rediscover these)

- Execution-layer semantics: `(live, paper)` and `(paper, fyers)` both mean
  PaperBroker ₹500,000. Real orders only possible at `(live, fyers)` — forbidden
  before P6/P7 sign-off. Data feed is independent of the broker field.
- Position rehydration restores positions + counters; v0.4.17 adds pending-signal
  restore (cards snapshot into signal rows at creation).
- Telegram sends are serialized/paced (1.2 s) with 429 Retry-After handling (v0.4.17).
- Watchdog v2: 15 s polls, `persist/heartbeat.log`, death bundles in
  `persist/death_reports/`, 5 restarts/day cap, `BACKEND_STOPPED_INTENTIONALLY`
  marker (2 h TTL) before graceful stops.
- Dev host history: cloud sandbox had 5 wipe/silent-death incidents (Sep 4–9).
  **Market-hours runtime = USER's local machine.** Sandbox = dev/verify bursts only.
- Sandbox/local must NEVER run the engine simultaneously (Telegram 409 / Fyers
  session conflicts). Single-instance invariant #4.

## USER actions currently pending

1. Local machine: clone repo, `./setup.sh`, enter Telegram creds in Settings,
   Fyers re-auth, start backend + watchdog. (PR #13 merge — DONE, see change log.)
2. Submit platform ticket (text: `download/platform_ticket_sandbox_silent_deaths.md`
   in session sidecar — copy into platform support portal).
3. Rotate the GitHub PAT shared in chat.

## How to update this file (SM only)

Append a row to the log below; never delete history.

### Change log
- 2026-09-09: seeded from session history (v0.4.17 pushed as PR #13; P1 day 1 done).
- 2026-09-09: **PR #13 (v0.4.17) merged to main @ 277a802 by USER** —
  pending-opportunity restart restore, Telegram 429 pacing + Retry-After,
  watchdog v2 + canaries now on main. Evidence: `git rev-parse HEAD` =
  `277a80254e260dc04fc65767bec6549d4f25fa1b` on `main` (verified in session);
  `RELEASE_NOTES_v0.4.17.md:94` (962 passed / 0 failed / 0 skipped).
- 2026-09-09: **PR #14 (harness v1) merged to main @ 277a802 by USER** —
  agentic harness scaffolding live in `docs/agentic/` (HARNESS.md, 6 role
  charters, 5 templates, `ci/backend-tests.yml`; enables D-002 / D-007).
  Evidence: same commit 277a802; `docs/agentic/` tree verified on main.
- 2026-09-09: **Sandbox re-verification on merged main: 962 passed / 0 failed /
  0 skipped (962/0/0)** — USER-reported run. Matches the v0.4.17 baseline
  (`RELEASE_NOTES_v0.4.17.md:94`). Phase ladder unchanged: P1 still IN
  PROGRESS (no new session-day evidence submitted with this update).
- 2026-09-09: **P1 pilot wave W1 planned** — watchdog `--check` one-pass
  health probe (script + tests only, read-only, exit 0/1, no restart loop)
  for the 09:00 pre-open smoke. Plan drafted: `docs/agentic/sprints/SPRINT_PLAN.md`
  (base @ 277a802). Status: DRAFT — awaiting ARCH verdict; DEV work blocked
  until verdict recorded in the plan's Debate record section.
