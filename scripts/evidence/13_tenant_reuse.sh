#!/usr/bin/env bash
# Evidence scenario 13: cross-tenant reuse; data never crosses tenants. Writes evidence/13_tenant_reuse/.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py 13 "$@"
