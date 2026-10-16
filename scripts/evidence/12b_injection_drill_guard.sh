#!/usr/bin/env bash
# Evidence scenario 12b: allowlist enforcement, both layers. Writes evidence/12b_injection_drill_guard/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 12b "$@"
