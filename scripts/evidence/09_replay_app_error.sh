#!/usr/bin/env bash
# Evidence scenario 09: hard failure evidence. Writes evidence/09_replay_app_error/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 09 "$@"
