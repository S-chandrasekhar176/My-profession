# CI referee — activation (one-time, USER only)

GitHub requires the `workflow` scope to push files under `.github/workflows/`
via API token. This token doesn't have it — by design we keep token scopes
minimal. Activation is one copy:

1. Open the repo on GitHub web (you are already logged in).
2. "Add file" → "Create new file" → path: `.github/workflows/backend-tests.yml`
3. Paste the full contents of `docs/agentic/ci/backend-tests.yml` → Commit
   (to this branch or main — either works; it triggers on PRs and main pushes).
4. Delete this staging folder in a later cleanup PR (optional).

From then on, every PR gets the independent machine verdict: backend suite
(962+ tests) + frontend `tsc --noEmit`. Agent green-claims do not count
without CI green (decision D-007).
