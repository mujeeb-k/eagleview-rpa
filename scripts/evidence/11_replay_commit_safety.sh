#!/usr/bin/env bash
# Evidence scenario 11: OUTCOME_UNCERTAIN after commit, no retry. Writes evidence/11_replay_commit_safety/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 11 "$@"
