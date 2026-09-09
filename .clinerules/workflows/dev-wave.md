# /dev-wave — implement one sprint task end-to-end

Act as DEV per `docs/agentic/charters/dev_charter.md`.

1. Read `docs/agentic/ROADMAP_STATE.md`, `DECISIONS.md`, then
   `docs/agentic/sprints/SPRINT_PLAN.md`.
2. Ask me which task number, or take the one named in this prompt: $ARGUMENTS
3. Create branch `feat|fix/<topic>-<today>` from latest origin/main.
4. Implement minimally. Bug fixes: regression test FIRST (fails on main).
5. Run `bash scripts/run_harness.sh pr`; fix until green; paste the tail.
6. Write `docs/agentic/sprints/REVIEW_PACKAGE.md` from the template with real
   evidence.
7. Stop and tell me: "Ready for QA-CODE review — open the Gemini panel and
   say: review per docs/agentic/charters/qa_code_charter.md".

Do not merge. Do not push to main. If blocked 3x with root causes, write
ESCALATION.md and stop.
