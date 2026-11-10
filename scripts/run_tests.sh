#!/usr/bin/env bash
# Run every test file in its own process (the files share module names and sys.path tweaks, so a single
# `unittest discover` run has cross-file collisions). Usage: scripts/run_tests.sh [pattern]
set -u
cd "$(dirname "$0")/.."
failed=()
for f in tests/test_${1:-*}.py; do
  mod="tests.$(basename "$f" .py)"
  if ! out=$(python3 -m unittest "$mod" 2>&1); then
    failed+=("$mod")
    echo "FAIL $mod"; echo "$out" | tail -15
  else
    echo "ok   $mod  ($(echo "$out" | grep -E '^Ran' ))"
  fi
done
if [ ${#failed[@]} -gt 0 ]; then echo; echo "Failed: ${failed[*]}"; exit 1; fi
echo "All test files passed."
