#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: install-botparty-client.sh [options]

Installs a verified offline release wheel, or builds locally only in explicit development mode.

Options:
  --user NAME                 Service account (default: botparty)
  --prefix PATH               Install prefix (default: /opt/botparty)
  --config PATH               Config path (default: /etc/botparty/config.yaml)
  --state-dir PATH            State path (default: /var/lib/botparty)
  --extras PROFILE            Locked profile: vision, hardware, gpio, serial,
                              mqtt, usb, telemetry, polly or google-tts;
                              comma-separated combinations are supported
  --device-groups LIST        Explicit device groups (default: none)
  --reuse-user                Allow an explicitly selected existing account
  --dry-run                   Validate and print the installation plan only
  --no-service                Do not create/start a systemd unit
  --no-apt                    Do not install OS prerequisites
  --no-streamer               Skip the verified video streamer download
  --streamer-manifest URL     Custom signed streamer manifest URL
  --streamer-public-key FILE  Public key for a custom streamer manifest
  --release-wheel FILE        Attested release wheel (required in production)
  --offline-wheelhouse DIR    Attested dependency wheelhouse (required in production)
  --development-install       Local, non-service install from an unverified checkout
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
REUSE_USER="false"
DRY_RUN="false"
DEVELOPMENT_INSTALL="false"
RELEASE_WHEEL=""
OFFLINE_WHEELHOUSE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) INSTALL_USER="${2:-}"; shift 2 ;;
    --prefix) PREFIX="${2:-}"; shift 2 ;;
    --config) CONFIG_PATH="${2:-}"; shift 2 ;;
    --state-dir) STATE_DIR="${2:-}"; shift 2 ;;
    --extras) EXTRAS="${2:-}"; shift 2 ;;
    --device-groups) DEVICE_GROUPS="${2:-}"; shift 2 ;;
    --reuse-user) REUSE_USER="true"; shift ;;
    --dry-run) DRY_RUN="true"; shift ;;
    --no-service) WITH_SERVICE="false"; shift ;;
    --no-apt) WITH_APT="false"; shift ;;
    --no-streamer) WITH_STREAMER="false"; shift ;;
    --streamer-manifest) STREAMER_MANIFEST="${2:-}"; shift 2 ;;
    --streamer-public-key) STREAMER_PUBLIC_KEY="${2:-}"; shift 2 ;;
    --release-wheel) RELEASE_WHEEL="${2:-}"; shift 2 ;;
    --offline-wheelhouse) OFFLINE_WHEELHOUSE="${2:-}"; shift 2 ;;
    --development-install) DEVELOPMENT_INSTALL="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
done

if [[ "$DEVELOPMENT_INSTALL" == "true" ]]; then
  WITH_SERVICE="false"
  WITH_STREAMER="false"
fi

reject_unsafe_characters() {
  local label="$1"
  local value="$2"
  if [[ -z "$value" ]] || [[ "$value" =~ [[:space:]] ]] || [[ "$value" == *$'\n'* ]] || [[ "$value" == *$'\r'* ]]; then
    echo "$label must be non-empty and contain no whitespace or control characters" >&2
    exit 1
  fi
}

canonical_path() {
  local label="$1"
  local value="$2"
  reject_unsafe_characters "$label" "$value"
  if [[ "$value" != /* ]]; then
    echo "$label must be absolute" >&2
    exit 1
  fi
  reject_symlink_components "$label" "$value"
  realpath -m -- "$value"
}

reject_symlink_components() {
  local label="$1"
  local value="$2"
  local current="$value"
  while [[ "$current" != "/" ]]; do
    if [[ -L "$current" ]]; then
      echo "$label must not contain symlink components: $current" >&2
      exit 1
    fi
    current="$(dirname "$current")"
  done
}

is_within() {
  local child="$1"
  local parent="$2"
  [[ "$child" == "$parent" || "$child" == "$parent/"* ]]
}

PREFIX="$(canonical_path --prefix "$PREFIX")"
CONFIG_PATH="$(canonical_path --config "$CONFIG_PATH")"
STATE_DIR="$(canonical_path --state-dir "$STATE_DIR")"
REPO_DIR="$(canonical_path source "$REPO_DIR")"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

for target in "$PREFIX" "$CONFIG_PATH" "$CONFIG_DIR" "$STATE_DIR"; do
  case "$target" in
    /|/etc|/var|/var/lib|/opt|/home|/root|/usr|/usr/local|/srv|/tmp)
      echo "Refusing unsafe installation target: $target" >&2
      exit 1
      ;;
  esac
done
reject_symlink_components --prefix "$PREFIX"
reject_symlink_components --config "$CONFIG_PATH"
reject_symlink_components --state-dir "$STATE_DIR"
if is_within "$PREFIX" "$STATE_DIR" || is_within "$STATE_DIR" "$PREFIX" || \
   is_within "$CONFIG_DIR" "$PREFIX" || is_within "$CONFIG_DIR" "$STATE_DIR" || \
   is_within "$REPO_DIR" "$PREFIX" || is_within "$REPO_DIR" "$STATE_DIR"; then
  echo "Prefix, config, state and source paths must not overlap" >&2
  exit 1
fi

if [[ ! "$INSTALL_USER" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]]; then
  echo "Invalid service account name" >&2
  exit 1
fi

requested_profiles=(production)
if [[ -n "$EXTRAS" ]]; then
  requested_profiles=()
  IFS=',' read -r -a extras_values <<< "$EXTRAS"
  for profile in "${extras_values[@]}"; do
    profile="$(printf '%s' "$profile" | xargs)"
    case "$profile" in
      vision|hardware|gpio|serial|mqtt|usb|telemetry|polly|google-tts) ;;
      *) echo "Unsupported extras profile: $profile" >&2; exit 1 ;;
    esac
    if [[ " ${requested_profiles[*]} " != *" $profile "* ]]; then
      requested_profiles+=("$profile")
    fi
  done
fi

IFS=',' read -r -a requested_groups <<< "$DEVICE_GROUPS"
valid_groups=()
for group in "${requested_groups[@]}"; do
  group="$(printf '%s' "$group" | xargs)"
  if [[ -z "$group" ]]; then
    continue
  fi
  if ! getent group "$group" >/dev/null; then
    echo "Requested device group does not exist: $group" >&2
    exit 1
  fi
  valid_groups+=("$group")
done

if id "$INSTALL_USER" >/dev/null 2>&1; then
  existing_uid="$(id -u "$INSTALL_USER")"
  existing_shell="$(getent passwd "$INSTALL_USER" | cut -d: -f7)"
  if [[ "$existing_uid" -eq 0 ]]; then
    echo "Refusing to install the service as root" >&2
    exit 1
  fi
  if [[ "$REUSE_USER" != "true" ]] && \
     { [[ "$INSTALL_USER" != "botparty" ]] || [[ "$existing_uid" -ge 1000 ]] || \
       [[ "$existing_shell" != */nologin && "$existing_shell" != */false ]]; }; then
    echo "Existing account is not a dedicated botparty system user; use --reuse-user only after review" >&2
    exit 1
  fi
fi

if [[ "$DRY_RUN" == "true" ]]; then
  printf 'Installation plan validated\nUser: %s\nPrefix: %s\nConfig: %s\nState: %s\nProfiles: %s\nGroups: %s\n' \
    "$INSTALL_USER" "$PREFIX" "$CONFIG_PATH" "$STATE_DIR" \
    "${requested_profiles[*]}" "${valid_groups[*]:-none}"
  exit 0
fi

if [[ "$DEVELOPMENT_INSTALL" != "true" ]]; then
  verified_commit="${BOTPARTY_VERIFIED_SOURCE_COMMIT:-}"
  if [[ ! "$verified_commit" =~ ^[0-9a-fA-F]{40}$ && ! "$verified_commit" =~ ^[0-9a-fA-F]{64}$ ]]; then
    echo "Production installation requires BOTPARTY_VERIFIED_SOURCE_COMMIT from bootstrap-install.sh" >&2
    exit 1
  fi
  if [[ -d "$REPO_DIR/.git" ]]; then
    actual_commit="$(git -C "$REPO_DIR" rev-parse HEAD)"
    if [[ "${actual_commit,,}" != "${verified_commit,,}" ]]; then
      echo "Verified source commit does not match the installer checkout" >&2
      exit 1
    fi
    if [[ -n "$(git -C "$REPO_DIR" status --porcelain --untracked-files=all)" ]]; then
      echo "Verified production checkout must be clean" >&2
      exit 1
    fi
  fi
  RELEASE_WHEEL="$(canonical_path --release-wheel "$RELEASE_WHEEL")"
  OFFLINE_WHEELHOUSE="$(canonical_path --offline-wheelhouse "$OFFLINE_WHEELHOUSE")"
  if [[ ! -f "$RELEASE_WHEEL" || -L "$RELEASE_WHEEL" ]]; then
    echo "Production installation requires a regular attested --release-wheel" >&2
    exit 1
  fi
  if [[ ! -d "$OFFLINE_WHEELHOUSE" || -L "$OFFLINE_WHEELHOUSE" ]]; then
    echo "Production installation requires a regular attested --offline-wheelhouse" >&2
    exit 1
  fi
  verified_wheel_sha256="${BOTPARTY_VERIFIED_WHEEL_SHA256:-}"
  if [[ ! "$verified_wheel_sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "Production installation requires BOTPARTY_VERIFIED_WHEEL_SHA256" >&2
    exit 1
  fi
  actual_wheel_sha256="$(sha256sum "$RELEASE_WHEEL" | awk '{print $1}')"
  if [[ "$actual_wheel_sha256" != "$verified_wheel_sha256" ]]; then
    echo "Attested release wheel digest does not match" >&2
    exit 1
  fi
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

run_as_service() {
  if [[ "$(id -u)" -eq 0 ]]; then
    runuser -u "$INSTALL_USER" -- "$@"
  else
    sudo -u "$INSTALL_USER" -- "$@"
  fi
}

if [[ "$WITH_APT" == "true" ]]; then
  $SUDO apt-get update
  $SUDO apt-get install -y python3 python3-venv python3-pip ffmpeg ca-certificates
fi

if ! id "$INSTALL_USER" >/dev/null 2>&1; then
  $SUDO useradd --system --home-dir "$STATE_DIR" --shell /usr/sbin/nologin "$INSTALL_USER"
fi
if [[ ${#valid_groups[@]} -gt 0 ]]; then
  group_list="$(IFS=,; printf '%s' "${valid_groups[*]}")"
  $SUDO usermod -a -G "$group_list" "$INSTALL_USER"
fi

$SUDO install -d -m 0755 -o root -g root "$PREFIX"
$SUDO install -d -m 0700 -o "$INSTALL_USER" -g "$INSTALL_USER" "$STATE_DIR"
$SUDO install -d -m 0750 -o root -g "$INSTALL_USER" "$(dirname "$CONFIG_PATH")"
STREAMER_DIR="$PREFIX/libexec"

BUILD_DIR="$(mktemp -d)"
trap 'rm -rf "$BUILD_DIR"' EXIT
if [[ "$DEVELOPMENT_INSTALL" == "true" ]]; then
  python3 -m venv "$BUILD_DIR/build-venv"
  "$BUILD_DIR/build-venv/bin/pip" install --require-hashes --no-deps \
    -r "$REPO_DIR/requirements/build-toolchain.txt"
  "$BUILD_DIR/build-venv/bin/pip" install --require-hashes \
    -r "$REPO_DIR/requirements/dev.txt"
  (cd "$REPO_DIR" && "$BUILD_DIR/build-venv/bin/python" -m build --no-isolation \
    --wheel --outdir "$BUILD_DIR/dist")
  WHEEL_PATH="$(find "$BUILD_DIR/dist" -maxdepth 1 -name '*.whl' -print -quit)"
else
  WHEEL_PATH="$RELEASE_WHEEL"
fi
if [[ -z "$WHEEL_PATH" ]]; then
  echo "Wheel build produced no artifact" >&2
  exit 1
fi
WHEEL_SHA256="$(sha256sum "$WHEEL_PATH" | awk '{print $1}')"
BUILD_ID="sha256-$WHEEL_SHA256"

STAGING_ROOT="$PREFIX/.install-staging.$$"
$SUDO install -d -m 0750 -o root -g "$INSTALL_USER" "$STAGING_ROOT"
STAGED_VENV="$STAGING_ROOT/venv"
$SUDO python3 -m venv "$STAGED_VENV"
if [[ "$DEVELOPMENT_INSTALL" != "true" ]]; then
  $SUDO "$STAGED_VENV/bin/pip" install --no-index --find-links "$OFFLINE_WHEELHOUSE" \
    --require-hashes --no-deps -r "$REPO_DIR/requirements/installer.txt"
fi
for lock_profile in "${requested_profiles[@]}"; do
  pip_source_args=()
  if [[ "$DEVELOPMENT_INSTALL" != "true" ]]; then
    pip_source_args=(--no-index --find-links "$OFFLINE_WHEELHOUSE")
  fi
  $SUDO "$STAGED_VENV/bin/pip" install "${pip_source_args[@]}" --require-hashes \
    -r "$REPO_DIR/requirements/${lock_profile}.txt"
done
if [[ "$DEVELOPMENT_INSTALL" == "true" ]]; then
  $SUDO "$STAGED_VENV/bin/pip" install --no-deps "$WHEEL_PATH"
else
  printf '%s @ file://%s \\\n    --hash=sha256:%s\n' "botparty-robot" "$WHEEL_PATH" "$WHEEL_SHA256" \
    >"$BUILD_DIR/release-wheel.txt"
  $SUDO "$STAGED_VENV/bin/pip" install --no-index --no-deps --require-hashes \
    -r "$BUILD_DIR/release-wheel.txt"
fi
$SUDO "$STAGED_VENV/bin/botparty-robot" --version
$SUDO install -m 0755 -o root -g root \
  "$REPO_DIR/scripts/botparty-service-launcher.sh" "$STAGING_ROOT/botparty-service-launcher.sh"

STAGED_CONFIG="$STAGING_ROOT/config.yaml"
if [[ -f "$CONFIG_PATH" && ! -L "$CONFIG_PATH" ]]; then
  $SUDO install -m 0640 -o root -g "$INSTALL_USER" "$CONFIG_PATH" "$STAGED_CONFIG"
elif [[ -e "$CONFIG_PATH" || -L "$CONFIG_PATH" ]]; then
  echo "Existing configuration must be a regular non-symlink file" >&2
  exit 1
else
  $SUDO install -m 0640 -o root -g "$INSTALL_USER" \
    "$REPO_DIR/config.example.yaml" "$STAGED_CONFIG"
fi

if [[ -n "$STREAMER_MANIFEST" ]] || [[ -n "$STREAMER_PUBLIC_KEY" ]]; then
  if [[ -z "$STREAMER_MANIFEST" ]] || [[ -z "$STREAMER_PUBLIC_KEY" ]]; then
    echo "Both --streamer-manifest and --streamer-public-key are required" >&2
    exit 1
  fi
fi

STAGED_STREAMER_DIR="$STAGING_ROOT/libexec"
$SUDO install -d -m 0755 -o root -g root "$STAGED_STREAMER_DIR"
if [[ "$WITH_STREAMER" == "true" ]]; then
  streamer_args=(--dir "$STAGED_STREAMER_DIR")
  if [[ -n "$STREAMER_MANIFEST" ]]; then
    streamer_args+=(--manifest-url "$STREAMER_MANIFEST" --public-key "$STREAMER_PUBLIC_KEY")
  fi
  $SUDO env BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID=0 \
    "$STAGED_VENV/bin/python" -m botparty_robot.artifacts \
    "${streamer_args[@]}"
fi

run_as_service env BOTPARTY_DEPLOYMENT_MODE=production \
  "$STAGED_VENV/bin/botparty-robot" --config "$STAGED_CONFIG" config validate
if [[ "$DEVELOPMENT_INSTALL" != "true" ]]; then
  $SUDO "$STAGED_VENV/bin/python" -m pip uninstall --yes pip
  if [[ -e "$STAGED_VENV/bin/pip" || -e "$STAGED_VENV/bin/pip3" ]]; then
    echo "Runtime pip removal was incomplete" >&2
    exit 1
  fi
fi

printf '%s  %s\n' "$WHEEL_SHA256" "$(basename "$WHEEL_PATH")" | \
  $SUDO tee "$STAGING_ROOT/installed-wheel.sha256" >/dev/null
$SUDO chmod 0644 "$STAGING_ROOT/installed-wheel.sha256"

if [[ "$WITH_SERVICE" == "true" ]]; then
  supplementary=""
  if [[ ${#valid_groups[@]} -gt 0 ]]; then
    supplementary="SupplementaryGroups=${valid_groups[*]}"
  fi
  device_allow="$(run_as_service "$STAGED_VENV/bin/botparty-robot" \
    --config "$STAGED_CONFIG" device-policy)"
  unit_path="/etc/systemd/system/${SERVICE_NAME}.service"
  staged_unit="$STAGING_ROOT/${SERVICE_NAME}.service"
  $SUDO tee "$staged_unit" >/dev/null <<EOF
[Unit]
Description=BotParty Robot Client
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

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
Environment=BOTPARTY_STREAMER_DIR=$STREAMER_DIR
Environment=BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID=0
Environment=BOTPARTY_DEPLOYMENT_MODE=production
Environment=BOTPARTY_PYTHON=$PREFIX/venv/bin/python
Environment=BOTPARTY_BUILD_ID=$BUILD_ID
ExecStart=$PREFIX/botparty-service-launcher.sh --config $CONFIG_PATH
Restart=on-failure
OOMPolicy=stop
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
MemoryHigh=1G
MemoryMax=1536M
TasksMax=256
ProtectProc=invisible
SystemCallArchitectures=native
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
DevicePolicy=closed
$device_allow
ReadWritePaths=$STATE_DIR
ReadOnlyPaths=$CONFIG_PATH $STREAMER_DIR

[Install]
WantedBy=multi-user.target
EOF
fi

ACTIVATION_STARTED="false"
ACTIVATION_COMPLETE="false"
rollback_stamp="$(date -u +%Y%m%dT%H%M%S.%NZ).$$"
rollback_venv="$PREFIX/venv.rollback.$rollback_stamp"
rollback_streamer="$PREFIX/libexec.rollback.$rollback_stamp"
rollback_launcher="$PREFIX/botparty-service-launcher.rollback.$rollback_stamp"
rollback_metadata="$PREFIX/installed-wheel.rollback.$rollback_stamp"
rollback_config="$STAGING_ROOT/config.before"
rollback_unit="$STAGING_ROOT/unit.before"
SERVICE_WAS_ACTIVE="false"
VENV_EXISTED_BEFORE="false"
STREAMER_EXISTED_BEFORE="false"
LAUNCHER_EXISTED_BEFORE="false"
METADATA_EXISTED_BEFORE="false"
VENV_SWAPPED="false"
STREAMER_SWAPPED="false"
LAUNCHER_SWAPPED="false"
METADATA_SWAPPED="false"

if [[ -d "$PREFIX/venv" ]]; then
  VENV_EXISTED_BEFORE="true"
fi
if [[ -d "$STREAMER_DIR" ]]; then
  STREAMER_EXISTED_BEFORE="true"
fi
if [[ -e "$PREFIX/botparty-service-launcher.sh" ]]; then
  LAUNCHER_EXISTED_BEFORE="true"
fi
if [[ -e "$PREFIX/installed-wheel.sha256" ]]; then
  METADATA_EXISTED_BEFORE="true"
fi

inject_failure() {
  if [[ "${BOTPARTY_TEST_SIGNAL_AT:-}" == "$1" ]]; then
    echo "injected termination signal at $1" >&2
    kill -TERM "$$"
  fi
  if [[ "${BOTPARTY_TEST_FAIL_AT:-}" == "$1" ]]; then
    echo "injected activation failure at $1" >&2
    return 97
  fi
}

rollback_activation() {
  if [[ "$ACTIVATION_STARTED" == "true" && "$ACTIVATION_COMPLETE" != "true" ]]; then
    set +e
    if [[ "$VENV_SWAPPED" == "true" ]]; then
      $SUDO rm -rf "$PREFIX/venv"
      if [[ "$VENV_EXISTED_BEFORE" == "true" && -e "$rollback_venv" ]]; then
        $SUDO mv "$rollback_venv" "$PREFIX/venv"
      fi
    fi
    if [[ "$STREAMER_SWAPPED" == "true" ]]; then
      $SUDO rm -rf "$STREAMER_DIR"
      if [[ "$STREAMER_EXISTED_BEFORE" == "true" && -e "$rollback_streamer" ]]; then
        $SUDO mv "$rollback_streamer" "$STREAMER_DIR"
      fi
    fi
    if [[ "$LAUNCHER_SWAPPED" == "true" ]]; then
      if [[ "$LAUNCHER_EXISTED_BEFORE" == "true" && -e "$rollback_launcher" ]]; then
        $SUDO mv -f "$rollback_launcher" "$PREFIX/botparty-service-launcher.sh"
      else
        $SUDO rm -f "$PREFIX/botparty-service-launcher.sh"
      fi
    fi
    if [[ "$METADATA_SWAPPED" == "true" ]]; then
      if [[ "$METADATA_EXISTED_BEFORE" == "true" && -e "$rollback_metadata" ]]; then
        $SUDO mv -f "$rollback_metadata" "$PREFIX/installed-wheel.sha256"
      else
        $SUDO rm -f "$PREFIX/installed-wheel.sha256"
      fi
    fi
    if [[ -e "$rollback_config" ]]; then
      $SUDO install -m 0640 -o root -g "$INSTALL_USER" "$rollback_config" "$CONFIG_PATH"
    elif [[ "${CONFIG_EXISTED_BEFORE:-false}" != "true" ]]; then
      $SUDO rm -f "$CONFIG_PATH"
    fi
    if [[ "$WITH_SERVICE" == "true" ]]; then
      if [[ -e "$rollback_unit" ]]; then
        $SUDO install -m 0644 -o root -g root "$rollback_unit" "$unit_path"
      elif [[ "${UNIT_EXISTED_BEFORE:-false}" != "true" ]]; then
        $SUDO rm -f "$unit_path"
      fi
      $SUDO systemctl daemon-reload
      if [[ "$SERVICE_WAS_ACTIVE" == "true" ]]; then
        $SUDO systemctl restart "${SERVICE_NAME}.service"
      fi
    fi
    echo "Installation activation failed; the previous release was restored" >&2
  fi
}

finish_install() {
  local status="$?"
  trap - EXIT INT TERM
  rollback_activation
  if [[ -n "${STAGING_ROOT:-}" ]]; then
    $SUDO rm -rf -- "$STAGING_ROOT"
  fi
  if [[ -n "${BUILD_DIR:-}" ]]; then
    rm -rf -- "$BUILD_DIR"
  fi
  exit "$status"
}
trap finish_install EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CONFIG_EXISTED_BEFORE="false"
if [[ -f "$CONFIG_PATH" ]]; then
  CONFIG_EXISTED_BEFORE="true"
  $SUDO cp -a "$CONFIG_PATH" "$rollback_config"
fi
UNIT_EXISTED_BEFORE="false"
if [[ "$WITH_SERVICE" == "true" && -f "$unit_path" ]]; then
  UNIT_EXISTED_BEFORE="true"
  $SUDO cp -a "$unit_path" "$rollback_unit"
fi
if [[ "$WITH_SERVICE" == "true" ]] && $SUDO systemctl is-active --quiet "${SERVICE_NAME}.service"; then
  SERVICE_WAS_ACTIVE="true"
fi

ACTIVATION_STARTED="true"
if [[ "$VENV_EXISTED_BEFORE" == "true" ]]; then
  $SUDO mv "$PREFIX/venv" "$rollback_venv"
fi
$SUDO mv "$STAGED_VENV" "$PREFIX/venv"
VENV_SWAPPED="true"
inject_failure venv
if [[ "$STREAMER_EXISTED_BEFORE" == "true" ]]; then
  $SUDO mv "$STREAMER_DIR" "$rollback_streamer"
fi
$SUDO mv "$STAGED_STREAMER_DIR" "$STREAMER_DIR"
STREAMER_SWAPPED="true"
inject_failure streamer
if [[ "$LAUNCHER_EXISTED_BEFORE" == "true" ]]; then
  $SUDO mv "$PREFIX/botparty-service-launcher.sh" "$rollback_launcher"
fi
$SUDO mv "$STAGING_ROOT/botparty-service-launcher.sh" "$PREFIX/botparty-service-launcher.sh"
LAUNCHER_SWAPPED="true"
inject_failure launcher
if [[ "$METADATA_EXISTED_BEFORE" == "true" ]]; then
  $SUDO mv "$PREFIX/installed-wheel.sha256" "$rollback_metadata"
fi
$SUDO mv "$STAGING_ROOT/installed-wheel.sha256" "$PREFIX/installed-wheel.sha256"
METADATA_SWAPPED="true"
inject_failure metadata
$SUDO install -m 0640 -o root -g "$INSTALL_USER" "$STAGED_CONFIG" "$CONFIG_PATH"
inject_failure config
if [[ "$WITH_SERVICE" == "true" ]]; then
  $SUDO install -m 0644 -o root -g root "$staged_unit" "$unit_path"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable "${SERVICE_NAME}.service"
  if [[ "$SERVICE_WAS_ACTIVE" == "true" ]]; then
    $SUDO systemctl restart "${SERVICE_NAME}.service"
  fi
  inject_failure unit
fi
$SUDO "$PREFIX/venv/bin/botparty-robot" --version
ACTIVATION_COMPLETE="true"

# Keep only the current release and two timestamped rollback generations.
mapfile -t old_rollbacks < <(find "$PREFIX" -maxdepth 1 -mindepth 1 \
  \( -name 'venv.rollback.*' -o -name 'libexec.rollback.*' \
     -o -name 'botparty-service-launcher.rollback.*' \
     -o -name 'installed-wheel.rollback.*' \) -printf '%T@ %p\n' | sort -nr | cut -d' ' -f2-)
if [[ ${#old_rollbacks[@]} -gt 8 ]]; then
  for old_rollback in "${old_rollbacks[@]:8}"; do
    $SUDO rm -rf -- "$old_rollback"
  done
fi

echo "Installed BotParty Robot Client for service user $INSTALL_USER"
echo "Config: $CONFIG_PATH"
echo "State:  $STATE_DIR"
if [[ "$WITH_STREAMER" == "true" ]]; then
  echo "Video streamer: $STREAMER_DIR/botparty-streamer"
fi
if [[ "$WITH_SERVICE" == "true" ]]; then
  echo "Edit and validate the config, then start ${SERVICE_NAME}.service"
fi
