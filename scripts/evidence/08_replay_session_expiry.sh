#!/usr/bin/env bash
# Evidence scenario 08: recovery plus the idempotency rule. Writes evidence/08_replay_session_expiry/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 08 "$@"
