#!/usr/bin/env bash
# Run Workflow Learner in the background at every login (systemd user service),
# so the Brave companion and autopilot schedules are always connected.
# Remove with:  systemctl --user disable --now workflow-learner
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
escaped="${project_dir//\\/\\\\}"
escaped="${escaped//\"/\\\"}"
cat > "$unit_dir/workflow-learner.service" <<UNIT
[Unit]
Description=Workflow Learner (local workflow recorder and autopilot)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
WorkingDirectory="$escaped"
ExecStart="$escaped/start.sh" --no-open
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now workflow-learner.service
echo "Workflow Learner now starts at login: http://127.0.0.1:8765"
