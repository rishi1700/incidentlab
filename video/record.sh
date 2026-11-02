#!/usr/bin/env bash
# Record one full take (scenes 2-6) in a single live session against the
# real lab and Meta Model API. Re-runnable: each run resets the lab first,
# and the lab is reset again afterwards, so it stays clean between takes.
#
# Usage: MODEL_API_KEY=... video/record.sh   (the key is never printed)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/video/.browsers"
VPY="$ROOT/video/.venv/bin/python"

if [ -z "${MODEL_API_KEY:-}" ]; then echo "record.sh: MODEL_API_KEY is not set" >&2; exit 1; fi
command -v docker >/dev/null || { echo "record.sh: docker not found" >&2; exit 1; }
[ -x "$ROOT/video/.bin/ttyd" ] || { echo "record.sh: video/.bin/ttyd missing" >&2; exit 1; }

RAW="$ROOT/video/out/raw"
mkdir -p "$RAW"
rm -f "$RAW"/*

cleanup() {
  kill "$SERVER_PID" "$TTYD_PID" 2>/dev/null || true
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

echo "--- starting demo server and terminal server"
.venv/bin/python web/server.py >"$RAW/server.log" 2>&1 &
SERVER_PID=$!
PS1='$ ' "$ROOT/video/.bin/ttyd" -p 7681 -W -t fontSize=26 bash --norc >"$RAW/ttyd.log" 2>&1 &
TTYD_PID=$!
wait_for_url http://127.0.0.1:8090/ 60
wait_for_url http://127.0.0.1:7681/ 60

echo "--- recording take"
"$VPY" video/record.py
echo "take recorded in $RAW"
