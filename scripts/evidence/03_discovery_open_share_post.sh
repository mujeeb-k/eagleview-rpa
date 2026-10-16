#!/usr/bin/env bash
# Evidence scenario 03: irreversible gate, approval handoff. Writes evidence/03_discovery_open_share_post/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 03 "$@"
