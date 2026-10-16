#!/usr/bin/env bash
# Evidence scenario 06: preflight validation, UI never touched. Writes evidence/06_replay_rejected_input/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 06 "$@"
