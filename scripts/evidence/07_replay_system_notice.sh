#!/usr/bin/env bash
# Evidence scenario 07: recoverable condition. Writes evidence/07_replay_system_notice/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 07 "$@"
