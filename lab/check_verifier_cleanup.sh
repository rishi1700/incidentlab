#!/bin/bash
# Verifier cleanup check: repair.py verify deletes its 1 MB test file after
# comparing hashes. Needs the lab running and healthy. Never calls the model.
# Usage: bash lab/check_verifier_cleanup.sh
set -u
cd "$(dirname "$0")/.."

UPLOADS="${INCIDENTLAB_DATA_MOUNT:-/srv/lab-data}/uploads"

before=$(ls "$UPLOADS" | wc -l)
python3 repair.py verify || { echo "verify failed, cleanup not attempted"; exit 1; }
after=$(ls "$UPLOADS" | wc -l)
if [ "$before" = "$after" ]; then
  echo "cleanup: no leftover ($before files before and after)"
else
  echo "WARNING: uploads/ went from $before to $after files across verify"
  exit 1
fi
