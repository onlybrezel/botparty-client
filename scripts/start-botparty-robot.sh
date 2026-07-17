#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_DIR"

PYTHON_BIN="$REPO_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

OTA_STATE_DIR="${BOTPARTY_OTA_STATE_DIR:-}"
if [[ -n "$OTA_STATE_DIR" ]]; then
  if prepared_python="$("$PYTHON_BIN" -m botparty_robot.ota prepare --state "$OTA_STATE_DIR")"; then
    PYTHON_BIN="$prepared_python"
  fi
fi

exec "$PYTHON_BIN" -m botparty_robot "$@"
