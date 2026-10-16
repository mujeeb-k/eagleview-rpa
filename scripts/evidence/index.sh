#!/usr/bin/env bash
# Writes evidence/README.md from every scenario's result.json.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py --index
