#!/usr/bin/env bash
# Evidence scenario 04: deterministic replay, typed outputs. Writes evidence/04_replay_success/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 04 "$@"
