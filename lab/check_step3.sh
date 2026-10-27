#!/bin/bash
# Milestone 1 step 3 check. Needs the lab running (docker compose up) and
# the test filesystem mounted. Never calls the model.
# Usage: bash lab/check_step3.sh
set -u
cd "$(dirname "$0")/.."

echo "=== 1. inject, repair directly, verify ==="
python3 lab/faults.py inject upload_dir_not_writable
python3 lab/faults.py status
python3 repair.py restore_upload_dir_access --yes --no-verify
python3 lab/faults.py status
python3 repair.py verify

echo "=== 2. declining leaves the fault in place ==="
python3 lab/faults.py inject upload_dir_not_writable
printf 'n\n' | python3 repair.py restore_upload_dir_access; echo "repair exit=$? (2 = declined)"
python3 lab/faults.py status
printf '' | python3 repair.py restore_upload_dir_access; echo "repair exit=$? (2 = declined, no input)"
python3 lab/faults.py status
python3 repair.py verify; echo "verify exit=$? (1 = still broken, expected)"

echo "=== 3. reset to healthy ==="
python3 lab/faults.py reset upload_dir_not_writable
python3 repair.py verify
