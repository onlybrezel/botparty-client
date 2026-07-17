#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: install-botparty-client.sh [options]

Builds one wheel from this checkout and installs it for a dedicated service user.

Options:
  --user NAME                 Service account (default: botparty)
  --prefix PATH               Install prefix (default: /opt/botparty)
  --config PATH               Config path (default: /etc/botparty/config.yaml)
  --state-dir PATH            State path (default: /var/lib/botparty)
  --extras PROFILE            Locked profile: vision, hardware, gpio, serial,
                              mqtt, usb, telemetry, polly or google-tts
  --device-groups LIST        Explicit device groups (default: none)
  --no-service                Do not create/start a systemd unit
  --no-apt                    Do not install OS prerequisites
  --no-streamer               Skip the verified video streamer download
  --streamer-manifest URL     Custom signed streamer manifest URL
  --streamer-public-key FILE  Public key for a custom streamer manifest
  -h, --help                  Show this help
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_USER="botparty"
PREFIX="/opt/botparty"
CONFIG_PATH="/etc/botparty/config.yaml"
STATE_DIR="/var/lib/botparty"
EXTRAS=""
DEVICE_GROUPS=""
WITH_SERVICE="true"
WITH_APT="true"
STREAMER_MANIFEST=""
STREAMER_PUBLIC_KEY=""
WITH_STREAMER="true"
SERVICE_NAME="botparty-robot"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) INSTALL_USER="${2:-}"; shift 2 ;;
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --config) CONFIG_PATH="${2:-}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:-}"; shift 2 ;;
    --extras) EXTRAS="${2:-}"; shift 2 ;;
    --device-groups) DEVICE_GROUPS="${2:-}"; shift 2 ;;
    --no-service) WITH_SERVICE="false"; shift ;;
    --no-apt) WITH_APT="false"; shift ;;
    --no-streamer) WITH_STREAMER="false"; shift ;;
    --streamer-manifest) STREAMER_MANIFEST="${2:-}"; shift 2 ;;
    --streamer-public-key) STREAMER_PUBLIC_KEY="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

if [[ "$WITH_APT" == "true" ]]; then
  $SUDO apt-get update
  $SUDO apt-get install -y python3 python3-venv python3-pip ffmpeg ca-certificates
fi

if ! id "$INSTALL_USER" >/dev/null 2>&1; then
  $SUDO useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$INSTALL_USER"
fi
if [[ "$(id -u "$INSTALL_USER")" -eq 0 ]]; then
  echo "Refusing to install the service as root" >&2
  exit 1
fi

IFS=',' read -r -a requested_groups <<< "$DEVICE_GROUPS"
valid_groups=()
for group in "${requested_groups[@]}"; do
  group="$(printf '%s' "$group" | xargs)"
  if [[ -n "$group" ]] && getent group "$group" >/dev/null; then
    valid_groups+=("$group")
  fi
done
if [[ ${#valid_groups[@]} -gt 0 ]]; then
  group_list="$(IFS=,; printf '%s' "${valid_groups[*]}")"
  $SUDO usermod -a -G "$group_list" "$INSTALL_USER"
fi

$SUDO install -d -m 0755 -o root -g root "$PREFIX"
$SUDO install -d -m 0700 -o "$INSTALL_USER" -g "$INSTALL_USER" "$STATE_DIR"
$SUDO install -d -m 0750 -o root -g "$INSTALL_USER" "$(dirname "$CONFIG_PATH")"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
python3 -m venv "$BUILD_DIR/build-venv"
"$BUILD_DIR/build-venv/bin/pip" install --upgrade "pip>=26.0" "build>=1.4.0"
(cd "$REPO_DIR" && "$BUILD_DIR/build-venv/bin/python" -m build --wheel --outdir "$BUILD_DIR/dist")
WHEEL_PATH="$(find "$BUILD_DIR/dist" -maxdepth 1 -name '*.whl' -print -quit)"
if [[ -z "$WHEEL_PATH" ]]; then
  echo "Wheel build produced no artifact" >&2
  exit 1
fi
sha256sum "$WHEEL_PATH" | $SUDO tee "$PREFIX/installed-wheel.sha256" >/dev/null

$SUDO rm -rf "$PREFIX/venv.new"
$SUDO python3 -m venv "$PREFIX/venv.new"
$SUDO "$PREFIX/venv.new/bin/pip" install --upgrade "pip>=26.0"
LOCK_PROFILE="production"
if [[ -n "$EXTRAS" ]]; then
  case "$EXTRAS" in
    vision|hardware|gpio|serial|mqtt|usb|telemetry|polly|google-tts) LOCK_PROFILE="$EXTRAS" ;;
    *) echo "Unsupported extras profile: $EXTRAS" >&2; exit 1 ;;
  esac
fi
$SUDO "$PREFIX/venv.new/bin/pip" install --require-hashes \
  -r "$REPO_DIR/requirements/${LOCK_PROFILE}.txt"
$SUDO "$PREFIX/venv.new/bin/pip" install --no-deps "$WHEEL_PATH"
$SUDO "$PREFIX/venv.new/bin/botparty-robot" --version
$SUDO rm -rf "$PREFIX/venv.previous"
if [[ -d "$PREFIX/venv" ]]; then
  $SUDO mv "$PREFIX/venv" "$PREFIX/venv.previous"
fi
$SUDO mv "$PREFIX/venv.new" "$PREFIX/venv"
$SUDO install -m 0755 -o root -g root \
  "$REPO_DIR/scripts/botparty-service-launcher.sh" "$PREFIX/botparty-service-launcher.sh"

if [[ ! -f "$CONFIG_PATH" ]]; then
  $SUDO install -m 0600 -o "$INSTALL_USER" -g "$INSTALL_USER" \
    "$REPO_DIR/config.example.yaml" "$CONFIG_PATH"
fi

if [[ -n "$STREAMER_MANIFEST" ]] || [[ -n "$STREAMER_PUBLIC_KEY" ]]; then
  if [[ -z "$STREAMER_MANIFEST" ]] || [[ -z "$STREAMER_PUBLIC_KEY" ]]; then
    echo "Both --streamer-manifest and --streamer-public-key are required" >&2
    exit 1
  fi
fi

if [[ "$WITH_STREAMER" == "true" ]]; then
  streamer_args=(--dir "$STATE_DIR/bin")
  if [[ -n "$STREAMER_MANIFEST" ]]; then
    streamer_args+=(--manifest-url "$STREAMER_MANIFEST" --public-key "$STREAMER_PUBLIC_KEY")
  fi
  $SUDO -u "$INSTALL_USER" "$PREFIX/venv/bin/python" -m botparty_robot.artifacts \
    "${streamer_args[@]}"
fi

if [[ "$WITH_SERVICE" == "true" ]]; then
  supplementary=""
  if [[ ${#valid_groups[@]} -gt 0 ]]; then
    supplementary="SupplementaryGroups=${valid_groups[*]}"
  fi
  device_allow=""
  for group in "${valid_groups[@]}"; do
    case "$group" in
      video) device_allow+=$'DeviceAllow=/dev/video0 rw\nDeviceAllow=/dev/video1 rw\nDeviceAllow=/dev/video2 rw\n' ;;
      audio) device_allow+=$'DeviceAllow=char-alsa rw\n' ;;
      dialout) device_allow+=$'DeviceAllow=/dev/ttyUSB0 rw\nDeviceAllow=/dev/ttyACM0 rw\n' ;;
      i2c) device_allow+=$'DeviceAllow=/dev/i2c-1 rw\n' ;;
      gpio) device_allow+=$'DeviceAllow=/dev/gpiochip0 rw\n' ;;
    esac
  done
  unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
  $SUDO tee "$unit_path" >/dev/null <<EOF
[Unit]
Description=BotParty Robot Client
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
NotifyAccess=main
User=$INSTALL_USER
Group=$INSTALL_USER
$supplementary
WorkingDirectory=$(dirname "$CONFIG_PATH")
Environment=PYTHONUNBUFFERED=1
Environment=BOTPARTY_CONFIG=$CONFIG_PATH
Environment=BOTPARTY_STATE_DIR=$STATE_DIR
Environment=BOTPARTY_OTA_STATE_DIR=$STATE_DIR/ota
Environment=BOTPARTY_STREAMER_DIR=$STATE_DIR/bin
Environment=BOTPARTY_PYTHON=$PREFIX/venv/bin/python
ExecStart=$PREFIX/botparty-service-launcher.sh --config $CONFIG_PATH
Restart=on-failure
RestartSec=5
WatchdogSec=20
TimeoutStopSec=20
KillMode=mixed
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectKernelLogs=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictSUIDSGID=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
DevicePolicy=closed
$device_allow
ReadWritePaths=$STATE_DIR

[Install]
WantedBy=multi-user.target
EOF
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "${SERVICE_NAME}.service"
fi

echo "Installed BotParty Robot Client for service user $INSTALL_USER"
echo "Config: $CONFIG_PATH"
echo "State:  $STATE_DIR"
if [[ "$WITH_STREAMER" == "true" ]]; then
  echo "Video streamer: $STATE_DIR/bin/botparty-streamer"
fi
if [[ "$WITH_SERVICE" == "true" ]]; then
  echo "Edit and validate the config, then start ${SERVICE_NAME}.service"
fi
