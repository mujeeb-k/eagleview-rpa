#!/usr/bin/env bash
# Evidence scenario 15: determinism over 20 runs. Writes evidence/15_replay_stability/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 15 "$@"
