#!/usr/bin/env bash
# Evidence scenario 01: LLM-driven discovery, artifact, grounding. Writes evidence/01_discovery_member_lookup/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 01 "$@"
