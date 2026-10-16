#!/usr/bin/env bash
# The demo video: mock bank only, and only with --record-video. Writes evidence/demo.mp4.
set -euo pipefail
cd "$(dirname "$0")/../.."
PYTHONPATH=. exec uv run python scripts/evidence/demo_video.py "$@"
