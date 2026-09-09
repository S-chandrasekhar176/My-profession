# QA-CODE REPORT — <branch name>

Reviewer: <engine/model> | Date: <date> | Diff base: origin/main@<sha>

## Verdict: PASS / FAIL  (FAIL = any BLOCKER or MAJOR)

## Findings
| # | Severity | Location | Finding | Suggested direction |
|---|---|---|---|---|
| 1 | BLOCKER/MAJOR/MINOR/NIT | file:line | <what is wrong> | <how to approach> |

## Checklist compliance (✓/✗ + one-line note each)
- [ ] Correctness vs plan traced in real code
- [ ] Data-source truthfulness (no silent wrong source / stale-as-fresh)
- [ ] Edge cases (None, zero-qty, expired token, null, tz, restart, dup-approval)
- [ ] No secrets/DB content in code, tests, logs, fixtures
- [ ] Matches surrounding patterns; no dead code/debug prints
- [ ] Tests assert new behavior (would fail on old code); regression present
- [ ] 5 invariants untouched

## Verification performed (not trusted from REVIEW_PACKAGE)
```
$ <commands you actually ran>
<key output>
```
