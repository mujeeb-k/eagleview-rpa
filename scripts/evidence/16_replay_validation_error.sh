#!/usr/bin/env bash
# Evidence scenario 16: app validation error is a business outcome. Writes evidence/16_replay_validation_error/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 16 "$@"
