#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
launcher_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$launcher_dir"
python3 - "$project_dir" "$launcher_dir" <<'PY'
from pathlib import Path
import sys
project, target = map(Path, sys.argv[1:])
escaped = str(project / 'start.sh').replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
(target / 'workflow-learner.desktop').write_text(f'''[Desktop Entry]
Type=Application
Name=Workflow Learner
Comment=Learn and replay workflows locally
Exec="{escaped}"
Terminal=true
Categories=Utility;Office;
Icon=system-run
''')
print('Workflow Learner launcher installed in your application menu.')
PY
