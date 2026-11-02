#!/usr/bin/env bash
# Render the cards, extract stills, assemble the scenes and join the video.
# Needs one take from video/record.sh. Re-runnable.
#
# Usage: video/build.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PLAYWRIGHT_BROWSERS_PATH="$ROOT/video/.browsers"
VPY="$ROOT/video/.venv/bin/python"

for f in video/out/raw/terminal.webm video/out/raw/page.webm video/out/raw/moments.json; do
  [ -f "$f" ] || { echo "build.sh: $f missing; run video/record.sh first" >&2; exit 1; }
done
"$VPY" -c "
import json, sys
moments = json.load(open('video/out/raw/moments.json'))
if 'terminal' not in moments or 'page' not in moments:
    sys.exit('moments.json is from an incomplete take; run video/record.sh first')
"

"$VPY" video/cards.py
"$VPY" video/assemble.py
