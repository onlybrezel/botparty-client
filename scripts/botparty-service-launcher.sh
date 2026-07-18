#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${BOTPARTY_PYTHON:-/opt/botparty/venv/bin/python}"
OTA_STATE_DIR="${BOTPARTY_OTA_STATE_DIR:-}"
PREFIX="$(dirname "$(dirname "$(dirname "$PYTHON_BIN")")")"

if [[ "${1:-}" == "--rollback-installer" ]]; then
  current="$PREFIX/venv"
  previous="$PREFIX/venv.previous"
  temporary="$PREFIX/.venv.rollback-current"
  for directory in "$current" "$previous"; do
    if [[ ! -d "$directory" ]] || [[ -L "$directory" ]] || [[ ! -x "$directory/bin/python" ]]; then
      echo "Installer rollback rejected: invalid environment $directory" >&2
      exit 2
    fi
  done
  if [[ -e "$temporary" ]]; then
    echo "Installer rollback rejected: temporary path already exists" >&2
    exit 2
  fi
  mv "$current" "$temporary"
  if ! mv "$previous" "$current"; then
    mv "$temporary" "$current"
    exit 2
  fi
  if ! mv "$temporary" "$previous"; then
    mv "$current" "$previous"
    mv "$temporary" "$current"
    exit 2
  fi
  echo "Installer environment rollback completed"
  exit 0
fi

if [[ -n "$OTA_STATE_DIR" ]]; then
  set +e
  prepared_python="$("$PYTHON_BIN" -m botparty_robot.ota prepare --state "$OTA_STATE_DIR")"
  prepare_status=$?
  set -e
  case "$prepare_status" in
    0) PYTHON_BIN="$prepared_python" ;;
    4) PYTHON_BIN="$prepared_python"; echo "BotParty OTA rollback completed" >&2 ;;
    3) ;;
    *) echo "BotParty OTA state is invalid; refusing to start" >&2; exit "$prepare_status" ;;
  esac
fi

exec "$PYTHON_BIN" -m botparty_robot "$@"
