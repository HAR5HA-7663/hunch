#!/usr/bin/env bash
# Re-create docs/demo.gif. The GIF is a real run replayed next to its own step log;
# the stopwatch follows the video clock, so nothing is sped up.
set -euo pipefail
cd "$(dirname "$0")"; mkdir -p raw
S="${AGENT_BROWSER_SESSION:-hunchdemo}"; ab() { agent-browser --session "$S" "$@"; }

ab set viewport 1000 720; ab open about:blank
ab record start "$PWD/raw/run.webm"
AGENT_BROWSER_SESSION="$S" ../examples/web_form.sh > raw/run.log
sleep 1.2; ab record stop
python3 - <<'PY'
import json
raw = open("raw/run.log").read()
steps = [json.loads(l) for l in raw.splitlines() if l.startswith('{"step"')]
json.dump({"steps": steps, "summary": json.loads(raw[raw.rfind("\n{\n") + 1:])}, open("raw/run.json", "w"))
PY

python3 -m http.server 8799 --bind 127.0.0.1 >/dev/null 2>&1 & SRV=$!; trap 'kill $SRV' EXIT; sleep 0.8
ab set viewport 1280 640; ab open about:blank
ab record start "$PWD/raw/player.webm" "http://127.0.0.1:8799/player.html"; sleep 9.5; ab record stop
ffmpeg -v error -y -ss 0.3 -t 8.2 -i raw/player.webm -loop 0 demo.gif -vf \
  "fps=10,scale=1100:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle"
