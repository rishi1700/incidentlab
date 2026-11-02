#!/usr/bin/env bash
# Record one full take (scenes 2-6) in a single live session against the
# real lab and Meta Model API. Re-runnable: each run resets the lab first,
# and the lab is reset again afterwards, so it stays clean between takes.
#
# Usage: MODEL_API_KEY=... video/record.sh [--manual-terminal]
#   --manual-terminal: skip the ttyd scene. After the lab is ready this
#   prints the four scene 2 commands for you to run yourself in your own
#   terminal (which you screen-record separately), waits for Enter, checks
#   that P3 is injected and an upload fails, then records the page session.
#   (the key is never printed)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/video/.browsers"
VPY="$ROOT/video/.venv/bin/python"

MANUAL_TERMINAL=0
if [ "${1:-}" = "--manual-terminal" ]; then
  MANUAL_TERMINAL=1
  shift
fi
if [ $# -gt 0 ]; then echo "record.sh: unknown argument: $1" >&2; exit 1; fi

if [ -z "${MODEL_API_KEY:-}" ]; then echo "record.sh: MODEL_API_KEY is not set" >&2; exit 1; fi
command -v docker >/dev/null || { echo "record.sh: docker not found" >&2; exit 1; }
if [ "$MANUAL_TERMINAL" = 0 ]; then
  [ -x "$ROOT/video/.bin/ttyd" ] || { echo "record.sh: video/.bin/ttyd missing" >&2; exit 1; }
fi

RAW="$ROOT/video/out/raw"
mkdir -p "$RAW"
rm -f "$RAW"/*

SERVER_PID=""
TTYD_PID=""
cleanup() {
  [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
  [ -n "$TTYD_PID" ] && kill "$TTYD_PID" 2>/dev/null || true
  python3 lab/faults.py reset all || true
}
trap cleanup EXIT

wait_for_url() { # $1=url $2=timeout-seconds
  "$VPY" - "$1" "$2" <<'EOF'
import sys, time, urllib.request
url, timeout = sys.argv[1], float(sys.argv[2])
deadline = time.monotonic() + timeout
while True:
    try:
        urllib.request.urlopen(url, timeout=5)
        sys.exit(0)
    except Exception:
        if time.monotonic() >= deadline:
            sys.exit(f"timed out waiting for {url}")
        time.sleep(2)
EOF
}

echo "--- clean lab, fresh containers"
python3 lab/faults.py reset all
docker compose -f lab/compose.yaml up -d --force-recreate
wait_for_url http://localhost:8080/health 180
python3 lab/verify.py

echo "--- starting demo server"
.venv/bin/python web/server.py >"$RAW/server.log" 2>&1 &
SERVER_PID=$!
if [ "$MANUAL_TERMINAL" = 0 ]; then
  PS1='$ ' "$ROOT/video/.bin/ttyd" -p 7681 -W -t fontSize=36 bash --norc >"$RAW/ttyd.log" 2>&1 &
  TTYD_PID=$!
fi
wait_for_url http://127.0.0.1:8090/ 60
if [ "$MANUAL_TERMINAL" = 0 ]; then
  wait_for_url http://127.0.0.1:7681/ 60
fi

if [ "$MANUAL_TERMINAL" = 1 ]; then
  echo "--- manual terminal scene: run these four commands in your own"
  echo "--- terminal (screen-record it for scene 2), then press Enter here."
  echo "python3 lab/verify.py"
  echo "python3 lab/faults.py inject P3"
  echo "python3 lab/verify.py"
  echo "curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/health"
  read -r _
  echo "--- checking the manual scene broke the lab"
  STATUS_OUT="$(python3 lab/faults.py status P3)"
  if ! printf '%s' "$STATUS_OUT" | grep -q "'tmp_app_user_can_write': False"; then
    echo "record.sh: P3 is not injected (tmp/ is still writable); not recording" >&2
    exit 1
  fi
  if ! printf '%s' "$STATUS_OUT" | grep -q "'app_user_can_write': True"; then
    echo "record.sh: uploads/ is not writable; expected exactly P3; not recording" >&2
    exit 1
  fi
  if python3 lab/verify.py; then
    echo "record.sh: uploads still succeed; the manual scene did not break the lab" >&2
    exit 1
  fi
  echo "--- P3 injected and uploads failing; recording the page session"
fi

echo "--- recording take"
if [ "$MANUAL_TERMINAL" = 1 ]; then
  "$VPY" video/record.py --skip-terminal
else
  "$VPY" video/record.py
fi
echo "take recorded in $RAW"
