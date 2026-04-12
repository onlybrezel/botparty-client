#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
usage: install-botparty-client.sh [options]

Lightweight bootstrap installer for BotParty Robot Client.

Options:
  --user <name>                 Service user (default: current user)
  --repo-dir <path>             Repo path (default: detected repo or ~/botparty-client)
  --repo-url <url>              Repo clone URL (default: https://github.com/onlybrezel/botparty-client)
  --branch <name>               Clone branch/tag (optional)
  --venv-dir <path>             Virtualenv path (default: <repo>/.venv)
  --python <bin>                Python executable (default: python3)
  --no-streamer                 Skip botparty-streamer install
  --streamer-version <tag>      Streamer version (default: active from stats endpoint)
  --streamer-dir <path>         Streamer install dir (default: <repo>/.botparty/bin)
  --no-service                  Skip systemd service setup
  --service-name <name>         Service name (default: botparty-robot)
  --no-apt                      Do not install missing apt packages
  --overwrite-config            Overwrite existing config.yaml from config.example.yaml
  --non-interactive             Disable whiptail prompts
  -h, --help                    Show this help

Examples:
  ./scripts/install-botparty-client.sh
  ./scripts/install-botparty-client.sh --overwrite-config
  ./scripts/install-botparty-client.sh --repo-dir "$HOME/botparty-client" --no-service
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTIVE_CONTROLLER_VERSION_URL="${BOTPARTY_CONTROLLER_ACTIVE_VERSION_URL:-https://stats.botparty.live/get_active_version.php?app=controller}"
ACTIVE_STREAMER_VERSION_URL="${BOTPARTY_STREAMER_ACTIVE_VERSION_URL:-https://stats.botparty.live/get_active_version.php?app=streamer}"
CURRENT_USER="$(id -un)"
INSTALL_USER="$CURRENT_USER"
REPO_URL="https://github.com/onlybrezel/botparty-client"
BRANCH=""
if [[ -f "$SCRIPT_REPO_DIR/requirements.txt" ]] && [[ -d "$SCRIPT_REPO_DIR/botparty_robot" ]]; then
  REPO_DIR="$SCRIPT_REPO_DIR"
else
  REPO_DIR="/home/$CURRENT_USER/botparty-client"
fi
VENV_DIR=""
PYTHON_BIN="python3"
WITH_STREAMER="true"
WITH_SERVICE="true"
WITH_APT="true"
OVERWRITE_CONFIG="false"
NON_INTERACTIVE="false"
STREAMER_VERSION=""
STREAMER_DIR=""
SERVICE_NAME="botparty-robot"
ARGS_COUNT=$#

fetch_active_version() {
  local url="$1"
  local raw=""
  raw="$(curl -fsSL --max-time 6 "$url" 2>/dev/null || true)"
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

ACTIVE_CONTROLLER_VERSION="$(fetch_active_version "$ACTIVE_CONTROLLER_VERSION_URL" || true)"
ACTIVE_STREAMER_VERSION="$(fetch_active_version "$ACTIVE_STREAMER_VERSION_URL" || true)"
if [[ -z "$STREAMER_VERSION" ]]; then
  STREAMER_VERSION="${ACTIVE_STREAMER_VERSION:-v0.1.0}"
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)
      INSTALL_USER="${2:-}"
      shift 2
      ;;
    --repo-dir)
      REPO_DIR="${2:-}"
      shift 2
      ;;
    --repo-url)
      REPO_URL="${2:-}"
      shift 2
      ;;
    --branch)
      BRANCH="${2:-}"
      shift 2
      ;;
    --venv-dir)
      VENV_DIR="${2:-}"
      shift 2
      ;;
    --python)
      PYTHON_BIN="${2:-}"
      shift 2
      ;;
    --no-streamer)
      WITH_STREAMER="false"
      shift
      ;;
    --streamer-version)
      STREAMER_VERSION="${2:-}"
      shift 2
      ;;
    --streamer-dir)
      STREAMER_DIR="${2:-}"
      shift 2
      ;;
    --no-service)
      WITH_SERVICE="false"
      shift
      ;;
    --service-name)
      SERVICE_NAME="${2:-}"
      shift 2
      ;;
    --no-apt)
      WITH_APT="false"
      shift
      ;;
    --overwrite-config)
      OVERWRITE_CONFIG="true"
      shift
      ;;
    --non-interactive)
      NON_INTERACTIVE="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$VENV_DIR" ]]; then
  VENV_DIR="$REPO_DIR/.venv"
fi
if [[ -z "$STREAMER_DIR" ]]; then
  STREAMER_DIR="$REPO_DIR/.botparty/bin"
fi

if ! id "$INSTALL_USER" >/dev/null 2>&1; then
  echo "Error: user does not exist: $INSTALL_USER" >&2
  exit 1
fi

if [[ "$(id -u)" -eq 0 ]]; then
  SUDO=""
else
  SUDO="sudo"
fi

maybe_interactive_prompts() {
  if [[ "$NON_INTERACTIVE" == "true" ]] || [[ "$ARGS_COUNT" -gt 0 ]] || [[ ! -t 0 ]]; then
    return
  fi
  if ! command -v whiptail >/dev/null 2>&1; then
    return
  fi

  if whiptail --yesno "Install botparty-streamer helper too?" 10 60; then
    WITH_STREAMER="true"
  else
    WITH_STREAMER="false"
  fi

  if whiptail --yesno "Create and enable systemd service (${SERVICE_NAME})?" 10 60; then
    WITH_SERVICE="true"
  else
    WITH_SERVICE="false"
  fi
  if [[ -f "$REPO_DIR/config.yaml" ]] && whiptail --yesno "Overwrite existing config.yaml with config.example.yaml?" 10 70; then
    OVERWRITE_CONFIG="true"
  fi
}

install_missing_apt_packages() {
  if [[ "$WITH_APT" != "true" ]]; then
    return
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Warning: apt-get not found, skipping package install" >&2
    return
  fi

  local packages=()
  command -v git >/dev/null 2>&1 || packages+=(git)
  command -v curl >/dev/null 2>&1 || packages+=(curl)
  command -v ffmpeg >/dev/null 2>&1 || packages+=(ffmpeg)
  command -v python3 >/dev/null 2>&1 || packages+=(python3)
  if ! python3 -m venv --help >/dev/null 2>&1; then
    packages+=(python3-venv)
  fi
  packages+=(python3-pip ca-certificates alsa-utils espeak mpg123)

  # De-duplicate while preserving order.
  local deduped=()
  local seen=" "
  local pkg
  for pkg in "${packages[@]}"; do
    if [[ "$seen" != *" $pkg "* ]]; then
      deduped+=("$pkg")
      seen+="$pkg "
    fi
  done

  echo "==> Installing OS packages"
  $SUDO apt-get update
  $SUDO apt-get install -y "${deduped[@]}"
}

ensure_repo_present() {
  if [[ -d "$REPO_DIR/.git" ]] && [[ -f "$REPO_DIR/requirements.txt" ]] && [[ -d "$REPO_DIR/botparty_robot" ]]; then
    return
  fi

  if [[ -d "$REPO_DIR" ]] && [[ -n "$(ls -A "$REPO_DIR" 2>/dev/null || true)" ]]; then
    echo "Error: $REPO_DIR exists but does not look like botparty-client" >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    if [[ "$WITH_APT" == "true" ]] && command -v apt-get >/dev/null 2>&1; then
      echo "==> Installing git"
      $SUDO apt-get update
      $SUDO apt-get install -y git
    else
      echo "Error: git not found and cannot auto-install (use --no-apt only if git exists)" >&2
      exit 1
    fi
  fi

  echo "==> Cloning botparty-client to $REPO_DIR"
  if [[ -z "$BRANCH" ]] && [[ -n "$ACTIVE_CONTROLLER_VERSION" ]]; then
    BRANCH="$ACTIVE_CONTROLLER_VERSION"
  fi

  if [[ -n "$BRANCH" ]]; then
    if ! git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$REPO_DIR"; then
      if [[ "$BRANCH" == "$ACTIVE_CONTROLLER_VERSION" ]]; then
        echo "Warning: active controller version $BRANCH is not available as git ref, cloning default branch" >&2
        git clone "$REPO_URL" "$REPO_DIR"
      else
        echo "Error: failed to clone branch/tag $BRANCH" >&2
        exit 1
      fi
    fi
  else
    git clone "$REPO_URL" "$REPO_DIR"
  fi
}

maybe_interactive_prompts
install_missing_apt_packages
ensure_repo_present

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: python binary not found: $PYTHON_BIN" >&2
  exit 1
fi

if [[ ! -x "$REPO_DIR/scripts/start-botparty-robot.sh" ]]; then
  chmod +x "$REPO_DIR/scripts/start-botparty-robot.sh" 2>/dev/null || true
fi

echo "==> BotParty bootstrap"
echo "repo:        $REPO_DIR"
echo "user:        $INSTALL_USER"
echo "python:      $PYTHON_BIN"
echo "venv:        $VENV_DIR"
echo "controller:  ${ACTIVE_CONTROLLER_VERSION:-unknown (stats unavailable)}"
echo "streamer:    ${STREAMER_VERSION}"
echo "streamer dir: ${STREAMER_DIR}"
echo "streamer:    $WITH_STREAMER"
echo "service:     $WITH_SERVICE"

echo "==> Creating Python virtualenv"
"$PYTHON_BIN" -m venv "$VENV_DIR"

echo "==> Installing Python dependencies"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt"

if [[ ! -f "$REPO_DIR/config.yaml" ]] || [[ "$OVERWRITE_CONFIG" == "true" ]]; then
  echo "==> Writing config.yaml from config.example.yaml"
  cp "$REPO_DIR/config.example.yaml" "$REPO_DIR/config.yaml"
fi

if [[ "$WITH_STREAMER" == "true" ]]; then
  echo "==> Installing botparty-streamer helper"
  BOTPARTY_STREAMER_DIR="$STREAMER_DIR" \
    "$REPO_DIR/scripts/install-botparty-streamer.sh" "$STREAMER_VERSION"
fi

if [[ "$WITH_SERVICE" == "true" ]]; then
  UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
  echo "==> Creating systemd unit: $UNIT_PATH"
  UNIT_CONTENT="[Unit]
Description=BotParty Robot Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${INSTALL_USER}
WorkingDirectory=${REPO_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${REPO_DIR}/scripts/start-botparty-robot.sh
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"
  printf "%s" "$UNIT_CONTENT" | $SUDO tee "$UNIT_PATH" >/dev/null
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable --now "${SERVICE_NAME}.service"
  echo "==> Service started: ${SERVICE_NAME}.service"
fi

echo
echo "Install complete."
echo "Next:"
echo "  1) Edit ${REPO_DIR}/config.yaml (claim token, hardware/video profile)"
echo "  2) If service enabled: $SUDO journalctl -u ${SERVICE_NAME}.service -f"
echo "  3) Manual run: ${REPO_DIR}/scripts/start-botparty-robot.sh"