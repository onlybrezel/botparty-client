#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${BOTPARTY_INSTALL_REPOSITORY:-https://github.com/onlybrezel/botparty-client.git}"
REF="${BOTPARTY_INSTALL_REF:-main}"
SOURCE_DIR="${BOTPARTY_INSTALL_SOURCE_DIR:-/opt/botparty/source}"

case "$REPOSITORY" in
  https://*) ;;
  *) echo "BOTPARTY_INSTALL_REPOSITORY must use HTTPS" >&2; exit 1 ;;
esac
if [[ ! "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || [[ "$REF" == *..* ]]; then
  echo "BOTPARTY_INSTALL_REF is invalid" >&2
  exit 1
fi
if [[ "$SOURCE_DIR" != /* ]] || [[ "$SOURCE_DIR" == "/" ]]; then
  echo "BOTPARTY_INSTALL_SOURCE_DIR must be an absolute non-root path" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=()
else
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required when the bootstrap is not run as root" >&2
    exit 1
  fi
  SUDO=(sudo)
fi

if ! command -v git >/dev/null 2>&1; then
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y git ca-certificates
fi

"${SUDO[@]}" mkdir -p "$(dirname "$SOURCE_DIR")"
if [[ -e "$SOURCE_DIR" ]]; then
  if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    echo "$SOURCE_DIR already exists and is not a Git checkout" >&2
    exit 1
  fi
  actual_repository="$("${SUDO[@]}" git -C "$SOURCE_DIR" remote get-url origin)"
  if [[ "$actual_repository" != "$REPOSITORY" ]]; then
    echo "$SOURCE_DIR uses a different Git origin" >&2
    exit 1
  fi
  if [[ -n "$("${SUDO[@]}" git -C "$SOURCE_DIR" status --porcelain --untracked-files=all)" ]]; then
    echo "$SOURCE_DIR contains local changes; refusing to overwrite them" >&2
    exit 1
  fi
  "${SUDO[@]}" git -C "$SOURCE_DIR" fetch --depth 1 origin "$REF"
  "${SUDO[@]}" git -C "$SOURCE_DIR" checkout --detach FETCH_HEAD
else
  "${SUDO[@]}" git clone --depth 1 --branch "$REF" "$REPOSITORY" "$SOURCE_DIR"
fi

exec "${SUDO[@]}" "$SOURCE_DIR/scripts/install-botparty-client.sh" "$@"
