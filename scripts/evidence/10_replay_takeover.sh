#!/usr/bin/env bash
# Evidence scenario 10: live-session handoff end to end; proposed patch. Writes evidence/10_replay_takeover/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 10 "$@"
