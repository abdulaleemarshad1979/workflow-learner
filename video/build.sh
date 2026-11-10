#!/usr/bin/env bash
# Rebuild the LinkedIn video: footage -> voiceover -> frames -> soundtrack -> MP4.
# Needs: ffmpeg, the project .venv (playwright), and internet for edge-tts + Google Fonts.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
PY="../.venv/bin/python"
[[ -x venv/bin/edge-tts ]] || { python3 -m venv venv && venv/bin/pip -q install edge-tts; }
export PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"
"$PY" capture.py footage ..                       # real screens from an isolated demo instance
venv/bin/python tts.py                            # voiceover + word timings (en-US-AndrewMultilingualNeural)
"$PY" render.py video 30                          # deterministic frames -> silent.mp4 + timeline.json
python3 mix.py                                    # voice at scene offsets + ducked music bed
ffmpeg -v error -y -i silent.mp4 -i soundtrack.wav -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k \
  -shortest -movflags +faststart ../docs/workflow-learner-linkedin.mp4
echo "Wrote docs/workflow-learner-linkedin.mp4"
