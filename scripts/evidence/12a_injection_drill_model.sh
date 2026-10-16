#!/usr/bin/env bash
# Evidence scenario 12a: the model and the injected note. Writes evidence/12a_injection_drill_model/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 12a "$@"
