# DECISIONS — append-only decision log

> Format: `### D-NNN (date): decision` + context + consequence.
> Agents: read before planning; do not re-open decided items unless new evidence
> invalidates one — then propose D-(N+1) referencing it.

### D-001 (2026-09-08): Roadmap P0–P9 adopted
Phases and gates as in `ROADMAP.md` / `ROADMAP_STATE.md`. Live pilot = USER-gated
(P6/P7), projected mid-to-late October 2026.

### D-002 (2026-09-09): Agentic harness = 6 roles, 2 engines
SM, ARCH, DEV, QA-CODE, QA-TEST, OPS (see `HARNESS.md`). GLM-5.3-Flash (Cline)
builds; Gemini (Antigravity) reviews/operates browser. Rationale: generator–
verifier diversity — the model that wrote code never approves it.

### D-003 (2026-09-09): OPS may contain, never fix mid-session
In-session: kill switches (`fyers_feed_enabled`, HALT_NEW_SIGNALS), backend
restart, evidence snapshots ONLY. Code fixes enter the normal DEV→QA loop
post-15:35. Rationale: paper phase rehearses live-money discipline.

### D-004 (2026-09-09): Evidence-based done
"Done" requires machine-generated evidence (test output, replay diff, screenshot)
linked from the claim. Agents re-run; they never trust prior claims.

### D-005 (2026-09-09): Retry protocol
QA failures loop back to DEV max 3 times; each retry requires a written root
cause + changed hypothesis (no blind reruns). Then escalate to USER.

### D-006 (2026-09-09): Runtime host = USER local machine; sandbox demoted
After 5 cloud-sandbox wipe/death incidents (Sep 4–9), market-hours runtime moves
to the USER's machine. GitHub is source of truth. IDEs are workshops, never the
runtime — backend runs as its own OS process.

### D-007 (2026-09-09): CI is the independent referee
GitHub Actions runs backend suite + frontend typecheck on every PR. Agent
green-claims do not count without CI green.

### D-008 (2026-09-09): Validation without wasting market days
Post-market fixes are validated the same evening: regression-test-first + replay
of recorded sessions + chaos subset; next-morning 15-min smoke covers only
feed/API-touching behavior. P1 gate counts every session as validation.

### D-009 (2026-09-09): Simulation engine is repo-owned infrastructure
No IDE provides it. `SimFeed`/`SimBroker` + injected clock will be built as
`core/sim/` per `SIM_ENGINE_SPEC.md` (staged: spec now, build in P1/P2 waves).

### D-010 (2026-09-09): Credential hygiene extended to artifacts
No credentials in repo (standing rule) AND none in screenshots/walkthroughs/
review packages. Local `data/*.db` holds encrypted `broker_credentials` — never
`git add -f`, never paste contents into agent chats. Public repo.
