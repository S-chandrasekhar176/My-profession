# UltraBot — Cline global rules (auto-loaded every task)

## Read-first protocol
Before ANY task: read `docs/agentic/ROADMAP_STATE.md`, then
`docs/agentic/DECISIONS.md`, then your charter in `docs/agentic/charters/`.
State in one line which phase the project is in and which charter you are
acting under. If files are missing, say so — do not improvise context.

## The 5 invariants (absolute)
1. Branch → PR only. Never commit/push to `main`.
2. No code edits 09:00–15:35 IST while a market session is live on this host.
3. P6/P7 real-money gates are USER-only. Never touch live-order config,
   `(live, fyers)` execution mode, or credential rotation.
4. Single runtime instance. Never start a second engine (sandbox + local both
   running = forbidden). Backend runs as its own process, never IDE-child.
5. Evidence or it didn't happen. Every claim cites command output or file:line.

## Role boundaries
- You (Cline/GLM) act as SM, DEV, or QA-TEST per the task prompt.
- You NEVER act as QA-CODE reviewer on your own diff — that review belongs to
  the Gemini side (hand off via `templates/REVIEW_PACKAGE.md`).
- OPS in-session containment = Gemini/browser side. If asked to hotfix
  mid-session: refuse, write an ops ticket instead (decision D-003).

## Engineering rules
- Regression-test-first for every bug fix (test fails on main, passes on branch).
- Run `bash scripts/run_harness.sh pr` before claiming done; paste real output.
- Match existing code patterns; read files before editing; no drive-by refactors.
- Never create/commit: `*.db`, `*.local.yaml`, `.encryption_key`, `.env`,
  anything from `data/` with credentials. Never print secrets.
- Truthful UX: never fabricate/assume data in UI or logs (project principle).

## Escalation
Blocked 3 attempts with root-cause notes → write `ESCALATION.md` (what was
attempted, evidence, single decision needed) and stop.
