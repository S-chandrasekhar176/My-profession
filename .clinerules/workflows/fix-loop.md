# /fix-loop — fix findings from a QA report (max 3 loops)

Act as DEV per `docs/agentic/charters/dev_charter.md`.

Input: qa findings in `docs/agentic/sprints/qa_code_report.md` (or a test
failure report). Loop count so far: $ARGUMENTS (default 1).

1. Read the findings. For EACH: state root cause + the hypothesis you are
   changing this round (D-005 — no blind reruns).
2. Fix on the same branch. Update/extend tests, including the failing case.
3. Run `bash scripts/run_harness.sh pr`; paste real output.
4. Update `REVIEW_PACKAGE.md` (append "Round N fixes" section).
5. Report back: "Round N done — hand back to Gemini QA-CODE" — or, if this is
   loop 3 and still failing, write `ESCALATION.md` and stop.
