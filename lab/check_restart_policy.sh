#!/bin/bash
# Restart-policy check: both services carry unless-stopped, and a db stopped
# with docker stop stays stopped. Leaves the lab healthy. Never calls the model.
# Usage: bash lab/check_restart_policy.sh
set -u
cd "$(dirname "$0")/.."

APP="${INCIDENTLAB_APP:-incidentlab-app-1}"
DB="${INCIDENTLAB_DB:-incidentlab-db-1}"

echo "=== 1. compose file is valid and both containers carry unless-stopped ==="
if docker compose -f lab/compose.yaml config >/dev/null; then
  echo "compose config: valid"
else
  echo "WARNING: compose config is invalid"
fi
for c in "$APP" "$DB"; do
  policy=$(docker inspect "$c" --format '{{.HostConfig.RestartPolicy.Name}}')
  echo "$c restart policy: $policy"
  [ "$policy" = "unless-stopped" ] || echo "WARNING: $c policy is $policy, expected unless-stopped"
done

echo "=== 2. a db stopped with docker stop stays stopped ==="
docker stop "$DB" >/dev/null
sleep 15
status=$(docker inspect "$DB" --format '{{.State.Status}}')
echo "db status 15s after docker stop: $status"
[ "$status" = "exited" ] && echo "stays stopped: yes" || echo "WARNING: db did not stay stopped"

echo "=== 3. back to healthy ==="
python3 lab/faults.py reset database_unavailable
python3 repair.py verify
