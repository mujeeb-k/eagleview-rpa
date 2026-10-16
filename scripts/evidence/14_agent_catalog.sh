#!/usr/bin/env bash
# Evidence scenario 14: agent-facing interface. Writes evidence/14_agent_catalog/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 14 "$@"
