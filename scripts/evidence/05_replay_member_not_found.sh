#!/usr/bin/env bash
# Evidence scenario 05: business outcome is not a failure. Writes evidence/05_replay_member_not_found/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 05 "$@"
