#!/usr/bin/env bash
# The held-out discovery check: one goal outside the evidence set, recorded pass or fail.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/driver.py --held-out
