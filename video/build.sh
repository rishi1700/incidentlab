#!/usr/bin/env bash
# Render the cards, extract stills, assemble the scenes and join the video.
# Needs one take from video/record.sh. Re-runnable.
#
# Usage: video/build.sh [--terminal-clip <file>]
#   --terminal-clip: use your own screen recording (.mov, any size) for
#   scene 2 instead of the take's ttyd footage. It is scaled to fit above
#   the caption strip, sped up to 22 s with a speed label, never cut.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/video/.browsers"
VPY="$ROOT/video/.venv/bin/python"

TERMINAL_CLIP=""
if [ "${1:-}" = "--terminal-clip" ]; then
  TERMINAL_CLIP="${2:-}"
  [ -n "$TERMINAL_CLIP" ] || { echo "build.sh: --terminal-clip needs a file" >&2; exit 1; }
  [ -f "$TERMINAL_CLIP" ] || { echo "build.sh: terminal clip not found: $TERMINAL_CLIP" >&2; exit 1; }
  shift 2
fi
if [ $# -gt 0 ]; then echo "build.sh: unknown argument: $1" >&2; exit 1; fi

for f in video/out/raw/page.webm video/out/raw/moments.json; do
  [ -f "$f" ] || { echo "build.sh: $f missing; run video/record.sh first" >&2; exit 1; }
done
if [ -z "$TERMINAL_CLIP" ]; then
  [ -f video/out/raw/terminal.webm ] \
    || { echo "build.sh: video/out/raw/terminal.webm missing; run video/record.sh first" >&2; exit 1; }
fi
TERMINAL_CLIP="$TERMINAL_CLIP" "$VPY" -c "
import json, os, sys
moments = json.load(open('video/out/raw/moments.json'))
if 'page' not in moments:
    sys.exit('moments.json is from an incomplete take; run video/record.sh first')
if 'terminal' not in moments and not os.environ.get('TERMINAL_CLIP'):
    sys.exit('moments.json is from an incomplete take; run video/record.sh first')
"

export TERMINAL_CLIP
"$VPY" video/cards.py
"$VPY" video/assemble.py
