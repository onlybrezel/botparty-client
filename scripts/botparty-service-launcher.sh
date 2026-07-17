#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${BOTPARTY_PYTHON:-/opt/botparty/venv/bin/python}"
OTA_STATE_DIR="${BOTPARTY_OTA_STATE_DIR:-}"

if [[ -n "$OTA_STATE_DIR" ]]; then
  if prepared_python="$("$PYTHON_BIN" -m botparty_robot.ota prepare --state "$OTA_STATE_DIR")"; then
    PYTHON_BIN="$prepared_python"
  fi
fi

exec "$PYTHON_BIN" -m botparty_robot "$@"
