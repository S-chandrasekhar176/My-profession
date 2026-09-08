# Branch & Git Conventions

## Branch naming

| Purpose | Pattern | Example |
|---|---|---|
| Hotfixes | `<day3>_hot_fixes_<YYYY-MM-DD>` | `tue_hot_fixes_2026-09-08` |
| Version implementations | `<day3>_v<version>_<phase>` | `mon_v0.4.13_m2`, `tue_v0.4.14_m2_5` |
| Docs-only (exception) | `<day3>_v<version>_docs_<topic>` | `tue_v0.4.14_docs_roadmap` |

`<day3>` = lowercase 3-letter weekday of the branch creation day (IST):
`mon tue wed thu fri sat sun`.

## Rules

1. **Never push to `main` directly.** Branch → PR → human merges.
2. **One fix = one commit.** Full test suite green before every push.
3. **Credentials never enter the repo** — code only. DB, keys, local config stay gitignored.
   The repo is public; treat every commit as public forever.
4. Branches are deleted after merge (local + remote hygiene via `--prune`).
5. Mid-run discipline: commits may be made during a live session, but **no deploys while
   positions are open** — deploy at the next flat/safe point.
6. Access tokens for pushes are short-lived, stored outside the repo
   (`~/.git-credentials`, mode 0600), and revoked on any suspicion.

## Post-incident note (2026-09-08)

The platform workspace recycled 4 times to date, twice stripping the nested `.git`.
Mitigations: push early and often (GitHub is the real backup), in-process 15-min DB backups
(v0.4.13), and credential storage outside the project directory.
