# UltraBot Agentic Harness — Master Reference

> **How the agents know what to do:** agents never see chat history. Their entire
> memory lives in THIS folder. Every session starts with reading:
> 1. `docs/agentic/ROADMAP_STATE.md` — where the project is, what phase, what % done
> 2. Your charter in `docs/agentic/charters/` — your role, your powers, your limits
> 3. `docs/agentic/DECISIONS.md` — decisions already taken (do not re-litigate)
>
> If it is not written in a file, it did not happen. If you learn something new,
> write it to a file before the session ends.

## The team (6 roles, 2 engines)

| Role | Engine | Charter |
|---|---|---|
| SM (Scrum Master) | Cline / GLM-5.3-Flash | `charters/sm_charter.md` |
| ARCH (Architect) | Antigravity / Gemini | `charters/architect_charter.md` |
| DEV (Implementer) | Cline / GLM-5.3-Flash | `charters/dev_charter.md` |
| QA-CODE (Reviewer) | Antigravity / Gemini | `charters/qa_code_charter.md` |
| QA-TEST (Tester) | Cline / GLM-5.3-Flash + harness script | `charters/qa_test_charter.md` |
| OPS (Market Operator) | Antigravity / Gemini (browser) | `charters/ops_charter.md` |

**Why two engines:** GLM writes code fast and cheap; Gemini reviews with a different
model family so the coder never reviews their own work. Never let one engine both
write AND approve the same change.

## The two tracks

```
DEV TRACK (per feature / per hotfix)
  SM plan → ARCH debate → SM final plan → DEV branch
    → QA-CODE review → QA-TEST battery → SM packages PR
    → USER merges → CI verifies → deploy at safe window
  Failures loop back to DEV. Max 3 loops, then escalate to USER.

OPS TRACK (daily, market hours)
  09:00 pre-open smoke → 09:15–15:30 monitor/contain only
    → 15:35 EOD reconciliation → tickets feed tomorrow's DEV track
  OPS NEVER edits code mid-session. Contain, snapshot, queue.
```

## Cadences

- **Per-PR (every merge):** full pytest suite + regression-test-first + targeted
  replay + chaos subset. Run locally via `bash scripts/run_harness.sh pr` and in CI.
- **Per-phase gate (phase exit):** everything above + full scenario matrix +
  multi-day replay + soak + EOD reconciliation audit + `phase_gate_report.md`
  reviewed by USER before next phase starts.

## The 5 invariants (no agent may break these)

1. Branch → PR only. No direct commits to `main`.
2. No code edits during a market session (09:00–15:35 IST). Contain, then queue.
3. P6/P7 (real money) are USER-only gates. No agent touches live-order config,
   broker mode `fyers` in the live execution layer, or credential rotation.
4. Single runtime instance. Exactly one host runs the engine. The IDEs are
   workshops, never the runtime — the backend runs as its own process.
5. Evidence or it didn't happen. Every "done" cites command output; every review
   cites `file:line`; every status change in ROADMAP_STATE.md links evidence.

## File map

```
docs/agentic/
  HARNESS.md            <- you are here (master reference)
  ROADMAP_STATE.md      <- live project status (SM owns, evidence-linked)
  DECISIONS.md          <- decision log (append-only)
  CHEATSHEET.md         <- copy-paste commands for the USER
  SIM_ENGINE_SPEC.md    <- spec for the simulation engine (planned build)
  charters/             <- 6 role definitions
  templates/            <- REVIEW_PACKAGE, qa_code_report, ops_ticket,
                           sprint_plan, phase_gate_report
.clinerules/            <- Cline auto-loads these (invariants + workflows)
.clinerules/workflows/  <- /dev-wave, /fix-loop, /sm-phase slash commands
docs/agentic/ci/        <- CI workflow (staged) + activation README
scripts/run_harness.sh  <- local test battery wrapper
```

## Escalation path

Any agent, after 3 failed loops or on any invariant violation, STOPS and writes
`ESCALATION.md` in the repo root: what was attempted, evidence, and the single
decision needed from USER. USER decisions always win.
