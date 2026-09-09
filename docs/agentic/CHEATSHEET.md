# USER CHEATSHEET — copy-paste these, nothing else to remember

> One window (Antigravity IDE), two panels: **Cline builds, Gemini checks.**

## Daily rhythm (4 messages)

| When | Panel | Paste this |
|---|---|---|
| Evening (build) | Cline | `/dev-wave <task # or description>` |
| Evening (review) | Gemini | `Act per docs/agentic/charters/qa_code_charter.md. Review docs/agentic/sprints/REVIEW_PACKAGE.md and the branch diff. Write docs/agentic/sprints/qa_code_report.md.` |
| If FAIL | Cline | `/fix-loop 1` (then 2, 3) |
| Merge | GitHub web | You click Merge. CI tests it independently. |

## Phase boundaries (occasional)

| Need | Panel | Paste |
|---|---|---|
| Plan next wave | Cline | `/sm-phase plan` |
| Debate the plan | Gemini | `Act per docs/agentic/charters/architect_charter.md. Debate docs/agentic/sprints/SPRINT_PLAN.md. Write your verdict into its Debate record.` |
| Update status | Cline | `/sm-phase update "<what changed>"` |
| Package PR | Cline | `/sm-phase package` |
| Phase exit | Cline | `/sm-phase gate` |

## Market day (OPS — Gemini panel)

| When | Paste |
|---|---|
| 09:00 | `Act per docs/agentic/charters/ops_charter.md. Run the pre-open checklist with browser smoke. Write docs/agentic/ops/<today>-preopen.md.` |
| Alert fires | `OPS: contain only (D-003). Read persist/death_reports/, classify, write an ops_ticket.md. Do NOT edit code.` |
| 15:35 | `OPS: run EOD reconciliation per charter. Write <today>-eod.md + ops tickets.` |

## Iron rules for me (the USER)
1. I am the only messenger — always Cline → Gemini → Cline, never parallel.
2. I merge. Nobody else.
3. If both chat panels are closed, the bot still trades — that is by design.
4. New machine/agent? It reads `docs/agentic/HARNESS.md` first. Nothing else needed.

## First-time setup (once)
1. Clone repo → run `./setup.sh` → backend `./start.sh` (own process, not from IDE).
2. Settings: Telegram creds → Fyers Connect (daily re-auth).
3. Cline: open this repo — `.clinerules/` auto-loads the rules above.
4. Gemini (Antigravity): no setup — charters are plain files it can read.
