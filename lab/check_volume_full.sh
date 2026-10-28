#!/bin/bash
# Milestone 2 volume_full check. Needs the lab running (docker compose up) and
# the test filesystem mounted. Never calls the model.
# Usage: bash lab/check_volume_full.sh
set -u
cd "$(dirname "$0")/.."

echo "=== 1. inject: uploads fail ==="
python3 lab/faults.py inject volume_full
python3 lab/faults.py status volume_full
echo -n "health during V1: "; curl -s -m 5 "${INCIDENTLAB_APP_URL:-http://localhost:8080}/health"; echo "(expect 503: V1 is completely full, unlike V3)"
python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"

echo "=== 2. repair alone frees the space, verify passes ==="
python3 repair.py clear_stale_temp_files --yes --no-verify
python3 lab/faults.py status volume_full
python3 repair.py verify

echo "=== 3. reset also returns a clean lab ==="
python3 lab/faults.py inject volume_full
python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
python3 lab/faults.py reset volume_full
python3 lab/faults.py status volume_full
python3 repair.py verify
