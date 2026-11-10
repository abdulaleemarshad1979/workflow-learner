#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements-lock.txt
  .venv/bin/python -m pip install --no-deps -e .
fi
exec .venv/bin/python -m workflow_learner "$@"
