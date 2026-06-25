#!/bin/bash
set -euo pipefail

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) — daily sync starting ==="

clover-connector sync
echo "--- sync complete"

opti-connector push
echo "--- push complete"

opti-connector decisions pull
echo "--- decisions pulled"

echo "=== done ==="
