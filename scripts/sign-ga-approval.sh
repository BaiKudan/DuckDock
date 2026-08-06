#!/usr/bin/env bash
set -euo pipefail

TASK_SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
TASK_REPO_ROOT="$(CDPATH= cd -- "$TASK_SCRIPT_DIR/.." && pwd)"
if [ -n "${DUCKDOCK_PYTHON:-}" ]; then
  TASK_PYTHON="$DUCKDOCK_PYTHON"
elif [ -x "$TASK_REPO_ROOT/backend/.venv/bin/python" ]; then
  TASK_PYTHON="$TASK_REPO_ROOT/backend/.venv/bin/python"
else
  TASK_PYTHON="python3"
fi
exec "$TASK_PYTHON" "$TASK_REPO_ROOT/backend/scripts/sign_ga_approval.py" "$@"
