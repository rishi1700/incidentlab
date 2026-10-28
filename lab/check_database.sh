#!/bin/bash
# Milestone 2 database_unavailable check. Needs the lab running
# (docker compose up) and the test filesystem mounted. Never calls the model.
# Usage: bash lab/check_database.sh
set -u
cd "$(dirname "$0")/.."

APP="${INCIDENTLAB_APP:-incidentlab-app-1}"
APP_STARTED_BEFORE=$(docker inspect "$APP" --format '{{.State.StartedAt}} {{.RestartCount}}')

echo "=== 1. inject: uploads fail ==="
python3 lab/faults.py inject database_unavailable
python3 lab/faults.py status database_unavailable
python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"

echo "=== 2. repair alone brings it back, verify passes ==="
python3 repair.py restore_database --yes --no-verify
python3 lab/faults.py status database_unavailable
python3 repair.py verify

echo "=== 3. reset does too ==="
python3 lab/faults.py inject database_unavailable
python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
python3 lab/faults.py reset database_unavailable
python3 lab/faults.py status database_unavailable
python3 repair.py verify

APP_STARTED_AFTER=$(docker inspect "$APP" --format '{{.State.StartedAt}} {{.RestartCount}}')
if [ "$APP_STARTED_BEFORE" = "$APP_STARTED_AFTER" ]; then
  echo "app container untouched (no restart): $APP_STARTED_AFTER"
else
  echo "WARNING: app container changed: before=$APP_STARTED_BEFORE after=$APP_STARTED_AFTER"
fi
