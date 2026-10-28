#!/bin/bash
# Milestone 3 step 1 checks for the eval variants. Needs the lab running
# (docker compose up) and the test filesystem mounted. Never calls the model.
# Usage: bash lab/check_eval_variants.sh [V2 V3 P2 P3 D2 D3 H2 H3]
# With no arguments all eight run; otherwise only the named ones.
set -u
cd "$(dirname "$0")/.."

APP="${INCIDENTLAB_APP:-incidentlab-app-1}"
APP_URL="${INCIDENTLAB_APP_URL:-http://localhost:8080}"
WANT="$*"
want() {
  [ -z "$WANT" ] && return 0
  case " $WANT " in
    *" $1 "*) return 0 ;;
    *) return 1 ;;
  esac
}

check_variant() { # <id> <repair>
  local id=$1 repair=$2
  echo "=== $id: inject fails, repair alone passes ==="
  python3 lab/faults.py inject "$id"
  python3 lab/faults.py status "$id"
  python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
  python3 repair.py "$repair" --yes --no-verify
  python3 lab/faults.py status "$id"
  python3 repair.py verify
  echo "=== $id: reset leaves a clean lab ==="
  python3 lab/faults.py inject "$id"
  python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
  python3 lab/faults.py reset "$id"
  python3 lab/faults.py status "$id"
  python3 repair.py verify
}

want V2 && check_variant V2 clear_stale_temp_files
want P2 && check_variant P2 restore_upload_dir_access
want P3 && check_variant P3 restore_upload_dir_access
want D2 && check_variant D2 restore_database
want D3 && check_variant D3 restore_database

if want V3; then
  check_variant V3 clear_stale_temp_files
  echo "=== V3 detail: /health passes while uploads fail ==="
  python3 lab/faults.py inject V3
  echo -n "health during V3: "; curl -s -m 5 "$APP_URL/health"; echo
  python3 lab/faults.py status V3
  python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
  python3 lab/faults.py reset V3
  python3 repair.py verify
fi

if want D2; then
  # check_variant D2 ran above; this pins the fast-while-paused /health.
  echo "=== D2 detail: /health answers fast while db is paused ==="
  python3 lab/faults.py inject D2
  curl -s -m 5 -o /dev/null -w "health while paused: %{http_code} in %{time_total}s\n" "$APP_URL/health"
  python3 repair.py verify; echo "verify exit=$? (1 = broken, expected)"
  python3 lab/faults.py reset D2
  python3 repair.py verify
fi

if want H2; then
  echo "=== H2: recovered restart, errors in log, verify passes ==="
  before=$(docker logs "$APP" 2>&1 | wc -l)
  python3 lab/faults.py inject H2
  python3 repair.py verify
  echo "--- error lines added to the app log by H2 ---"
  newlog=$(docker logs "$APP" 2>&1 | tail -n +$((before + 1)))
  echo "$newlog" | grep -iE 'connection|refused|translate|operationalerror' | tail -3
  if echo "$newlog" | grep -qiE 'connection|refused|translate|operationalerror'; then
    echo "planted errors present: yes"
  else
    echo "WARNING: no errors in app log"
  fi
fi

if want H3; then
  echo "=== H3: cleaned-up V1, old errors in log, verify passes ==="
  before=$(docker logs "$APP" 2>&1 | wc -l)
  python3 lab/faults.py inject H3
  python3 repair.py verify
  echo "--- error lines added to the app log by H3 ---"
  newlog=$(docker logs "$APP" 2>&1 | tail -n +$((before + 1)))
  echo "$newlog" | grep -i 'no space left' | tail -3
  if echo "$newlog" | grep -qi 'no space left'; then
    echo "planted errors present: yes"
  else
    echo "WARNING: no 'No space left' errors in app log"
  fi
fi
