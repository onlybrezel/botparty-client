#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

ACTIVE_VERSION_URL="${BOTPARTY_ACTIVE_VERSION_URL:-https://stats.botparty.live/get_active_version.php?app=streamer}"
VERSION=""
ARCH="auto"
BASE_URL="${BOTPARTY_STREAMER_URL:-http://dl.botparty.live}"
INSTALL_DIR="${BOTPARTY_STREAMER_DIR:-$REPO_DIR/.botparty/bin}"
TARGET="$INSTALL_DIR/botparty-streamer"
VERSION_FILE="$INSTALL_DIR/botparty-streamer.version"

fetch_active_version() {
  local raw=""
  if ! raw="$(curl -fsSL --max-time 6 "$ACTIVE_VERSION_URL" 2>/dev/null || true)"; then
    return 1
  fi
  raw="$(printf "%s" "$raw" | tr -d '\r' | head -n 1 | xargs || true)"
  if [[ -z "$raw" ]]; then
    return 1
  fi
  if [[ "$raw" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9._]+)?$ ]]; then
    printf "%s" "$raw"
    return 0
  fi
  if [[ "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9._]+)?$ ]]; then
    printf "v%s" "$raw"
    return 0
  fi
  return 1
}

usage() {
  cat >&2 <<'EOF'
usage: install-botparty-streamer.sh [version] [--arch amd64|arm64|arm|auto] [--dir <repo>/.botparty/bin] [--url http://dl.botparty.live]

Supported architectures:
  amd64   x86-64
  arm64   64-bit ARM  (Raspberry Pi 4/5 in 64-bit mode)
  arm     32-bit ARMv7 (Raspberry Pi 2/3 or 64-bit Pi in 32-bit OS)
  auto    detect from uname -m  (default)

If no version is given, active version is fetched from:
  https://stats.botparty.live/get_active_version.php?app=streamer

Examples:
  ./scripts/install-botparty-streamer.sh
  ./scripts/install-botparty-streamer.sh v0.1.0 --arch arm64
  ./scripts/install-botparty-streamer.sh --arch arm --dir ./custom-bin
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arch)
      ARCH="${2:-}"
      shift 2
      ;;
    --dir)
      INSTALL_DIR="${2:-}"
      shift 2
      ;;
    --url)
      BASE_URL="${2:-}"
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
      VERSION="$1"
      shift
      ;;
  esac
done

if [[ -z "$VERSION" ]]; then
  VERSION="$(fetch_active_version || true)"
fi
if [[ -z "$VERSION" ]]; then
  VERSION="v0.1.0"
  echo "Warning: could not fetch active streamer version, falling back to $VERSION" >&2
fi

if [[ "$ARCH" == "auto" ]]; then
  ARCH="$(uname -m)"
fi

case "$ARCH" in
  aarch64|arm64|linux-arm64|rpi|raspberrypi|pi)
    ASSET_ARCH="linux-arm64"
    ;;
  x86_64|amd64|linux-amd64)
    ASSET_ARCH="linux-amd64"
    ;;
  armv7l|armhf|arm7|linux-arm|armv7)
    ASSET_ARCH="linux-arm"
    ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

ASSET="botparty-streamer-${VERSION}-${ASSET_ARCH}"
URL="${BASE_URL%/}/${ASSET}"
TARGET="$INSTALL_DIR/botparty-streamer"
VERSION_FILE="$INSTALL_DIR/botparty-streamer.version"

mkdir -p "$INSTALL_DIR"

if [[ -x "$TARGET" ]] && [[ -f "$VERSION_FILE" ]]; then
  CURRENT_VERSION="$(tr -d '\r' < "$VERSION_FILE" | head -n 1 | xargs || true)"
  if [[ "$CURRENT_VERSION" == "$VERSION" ]]; then
    echo "botparty-streamer already up to date: $VERSION"
    exit 0
  fi
fi

echo "Downloading $URL"
curl -fL "$URL" -o "$TARGET"
chmod +x "$TARGET"
printf '%s\n' "$VERSION" > "$VERSION_FILE"

echo
echo "Installed botparty-streamer to $TARGET"
echo "Recorded version in $VERSION_FILE"
if ! "$TARGET" --help >/dev/null 2>&1; then
  echo "Warning: binary installed but --help returned non-zero" >&2
fi

if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
  echo "Note: add $INSTALL_DIR to PATH to use botparty-streamer directly" >&2
fi
