# /sm-phase — plan or advance the current phase

Act as SM per `docs/agentic/charters/sm_charter.md`.

Mode: $ARGUMENTS (`plan` a new phase wave | `update` status | `package` a PR |
`gate` a phase-gate report)

1. Read `docs/agentic/ROADMAP_STATE.md` + `DECISIONS.md` + `docs/ROADMAP.md`.
2. `plan`: draft `docs/agentic/sprints/SPRINT_PLAN.md` from the template for
   the current phase's next wave; then tell me to get ARCH's verdict in the
   Gemini panel: "debate docs/agentic/sprints/SPRINT_PLAN.md per
   docs/agentic/charters/architect_charter.md". Do NOT start DEV work before
   the verdict is recorded in the plan's Debate record section.
3. `update`: append evidence-linked change-log rows to ROADMAP_STATE.md.
4. `package`: prepare PR title/body from release notes + evidence links.
5. `gate`: produce `phase_gate_report.md` — gate criteria scorecard with
   evidence per row, then ask me for the exit decision.
