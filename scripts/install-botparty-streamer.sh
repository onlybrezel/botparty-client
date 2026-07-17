#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_BIN="${BOTPARTY_PYTHON:-python3}"
MANIFEST_URL="${BOTPARTY_STREAMER_MANIFEST_URL:-}"
PUBLIC_KEY="${BOTPARTY_STREAMER_PUBLIC_KEY_FILE:-}"
INSTALL_DIR="${BOTPARTY_STREAMER_DIR:-$REPO_DIR/.botparty/bin}"
VERSION=""

usage() {
  cat >&2 <<'EOF'
usage: install-botparty-streamer.sh [version] [--dir PATH]
       install-botparty-streamer.sh [version] --manifest-url URL --public-key FILE [--dir PATH]

Without custom release options, installs the official pinned streamer for this
machine. Custom releases require an Ed25519-signed manifest and public key.
Architecture, length, SHA-256, ELF format and version are always verified.

Environment equivalents:
  BOTPARTY_STREAMER_MANIFEST_URL
  BOTPARTY_STREAMER_PUBLIC_KEY_FILE
  BOTPARTY_STREAMER_DIR
  BOTPARTY_PYTHON
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest-url)
      MANIFEST_URL="${2:-}"
      shift 2
      ;;
    --public-key)
      PUBLIC_KEY="${2:-}"
      shift 2
      ;;
    --dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -n "$VERSION" ]]; then
        echo "Only one version may be specified" >&2
        exit 1
      fi
      VERSION="$1"
      shift
      ;;
  esac
done

if { [[ -n "$MANIFEST_URL" ]] && [[ -z "$PUBLIC_KEY" ]]; } || \
  { [[ -z "$MANIFEST_URL" ]] && [[ -n "$PUBLIC_KEY" ]]; }; then
  echo "Custom streamer installation requires both manifest URL and public key" >&2
  exit 1
fi

args=(
  -m botparty_robot.artifacts
  --dir "$INSTALL_DIR"
)
if [[ -n "$MANIFEST_URL" ]]; then
  args+=(--manifest-url "$MANIFEST_URL" --public-key "$PUBLIC_KEY")
fi
if [[ -n "$VERSION" ]]; then
  args+=(--version "$VERSION")
fi

cd "$REPO_DIR"
exec "$PYTHON_BIN" "${args[@]}"
