#!/usr/bin/env bash
# Evidence scenario 02: write flow, staged risk. Writes evidence/02_discovery_open_share_review/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 02 "$@"
