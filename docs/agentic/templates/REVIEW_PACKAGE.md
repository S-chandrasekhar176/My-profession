# REVIEW PACKAGE — <branch name> (DEV → QA handoff)

> Fill every section. QA reads the real diff; this file explains intent and
> carries evidence. Claims without evidence will be rejected.

## Wave
- Sprint/task: <id from SPRINT_PLAN.md>
- Plan source: <PLAN_FINAL.md / ticket path>

## What changed (intent, not diff dump)
- <bullet per logical change, one line each>

## Files touched
- `<path>` — <why> (list ONLY real changes; no drive-by edits)

## Evidence (commands actually run + key output lines)
```
$ bash scripts/run_harness.sh pr
<paste tail: N passed / 0 failed>
```
- Regression test for bug: <test name> — fails on main, passes here: <proof>
- CI run: <link> (if pushed)

## Risks / limitations
- <what this change does NOT cover; rollout notes; DB/config migration needs>

## Rollback
- Single-commit revert works? <yes/no — if no, explain>
