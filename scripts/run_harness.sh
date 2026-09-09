#!/usr/bin/env bash
# UltraBot test battery wrapper.
#   scripts/run_harness.sh pr      # per-PR battery (full suite + typecheck)
#   scripts/run_harness.sh quick   # backend suite only
# Evidence rule: pipe through `tee docs/agentic/sprints/test_report.md` when
# run for a review package. Exit code 0 = green.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$ROOT/ultrabot-web/backend"
MODE="${1:-pr}"
FAIL=0

echo "=== UltraBot harness ($MODE) $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "--- [1/2] backend suite ---"
if [ -d "$BACKEND/venv" ]; then
  # shellcheck disable=SC1091
  source "$BACKEND/venv/bin/activate"
elif [ -d "$ROOT/../venv" ]; then
  # sandbox layout: bot_analysis/venv next to the clone
  # shellcheck disable=SC1091
  source "$ROOT/../venv/bin/activate"
fi
(cd "$BACKEND" && python -m pytest tests/ -q --tb=short 2>&1 | tail -15) || FAIL=1

if [ "$MODE" = "pr" ]; then
  echo "--- [2/2] frontend typecheck ---"
  if command -v bun >/dev/null 2>&1; then
    (cd "$ROOT" && bun run tsc --noEmit 2>&1 | tail -5) || FAIL=1
  elif command -v npx >/dev/null 2>&1; then
    (cd "$ROOT" && npx tsc --noEmit 2>&1 | tail -5) || FAIL=1
  else
    echo "SKIP: no bun/npx found (typecheck runs in CI regardless)"
  fi
else
  echo "--- [2/2] skipped (quick mode) ---"
fi

if [ "$FAIL" -eq 0 ]; then
  echo "=== HARNESS GREEN ==="
else
  echo "=== HARNESS RED — do not claim done ==="
fi
exit "$FAIL"
