#!/usr/bin/env bash
set -euo pipefail

VERSION="v0.1.0"
ARCH="auto"
BASE_URL="${BOTPARTY_STREAMER_URL:-http://dl.botparty.live}"
INSTALL_DIR="${BOTPARTY_STREAMER_DIR:-/home/pi/bin}"
TARGET="$INSTALL_DIR/botparty-streamer"

usage() {
  cat >&2 <<'EOF'
usage: install-botparty-streamer.sh [version] [--arch amd64|arm64|arm|auto] [--dir /home/pi/bin] [--url http://dl.botparty.live]

Supported architectures:
  amd64   x86-64
  arm64   64-bit ARM  (Raspberry Pi 4/5 in 64-bit mode)
  arm     32-bit ARMv7 (Raspberry Pi 2/3 or 64-bit Pi in 32-bit OS)
  auto    detect from uname -m  (default)

Examples:
  ./scripts/install-botparty-streamer.sh
  ./scripts/install-botparty-streamer.sh v0.1.0 --arch arm64
  ./scripts/install-botparty-streamer.sh --arch arm --dir /home/pi/bin
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

mkdir -p "$INSTALL_DIR"
echo "Downloading $URL"
curl -fL "$URL" -o "$TARGET"
chmod +x "$TARGET"

echo
echo "Installed botparty-streamer to $TARGET"
if ! "$TARGET" --help >/dev/null 2>&1; then
  echo "Warning: binary installed but --help returned non-zero" >&2
fi

if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
  echo "Note: add $INSTALL_DIR to PATH to use botparty-streamer directly" >&2
fi
