#!/usr/bin/env bash
# The approved lookup's own discovery, verify and probe runs, copied from runs/ by the ids in its
# provenance. Writes evidence/approved_lookup_provenance/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py --provenance
