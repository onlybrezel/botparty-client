#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-v0.1.0}"
ARCH="$(uname -m)"
BASE_URL="${BOTPARTY_GSTREAMER_PUBLISHER_URL:-http://dl.botparty.live}"
INSTALL_DIR="${BOTPARTY_GSTREAMER_PUBLISHER_DIR:-$HOME/bin}"
TARGET="$INSTALL_DIR/gstreamer-publisher"

case "$ARCH" in
  aarch64|arm64)
    ASSET_ARCH="linux-arm64"
    ;;
  x86_64|amd64)
    ASSET_ARCH="linux-amd64"
    ;;
  *)
    echo "Unsupported architecture: $ARCH" >&2
    exit 1
    ;;
esac

ASSET="botparty-gstreamer-publisher-${VERSION}-${ASSET_ARCH}"
URL="${BASE_URL%/}/${ASSET}"

mkdir -p "$INSTALL_DIR"
echo "Downloading $URL"
curl -fL "$URL" -o "$TARGET"
chmod +x "$TARGET"

echo
echo "Installed gstreamer-publisher to $TARGET"
"$TARGET" --help >/dev/null || true
