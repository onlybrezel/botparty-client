#!/usr/bin/env bash
set -euo pipefail

REPOSITORY="${BOTPARTY_INSTALL_REPOSITORY:-https://github.com/onlybrezel/botparty-client.git}"
EMBEDDED_REF="__BOTPARTY_RELEASE_REF__"
EMBEDDED_ALLOWED_SIGNERS="__BOTPARTY_RELEASE_ALLOWED_SIGNERS_BASE64__"
EMBEDDED_BUNDLES="__BOTPARTY_RELEASE_BUNDLES_BASE64__"
UNSET_REF_MARKER="__BOTPARTY_RELEASE_"'REF__'
UNSET_ALLOWED_SIGNERS_MARKER="__BOTPARTY_RELEASE_ALLOWED_SIGNERS_"'BASE64__'

REF="${BOTPARTY_INSTALL_REF:-}"
SOURCE_DIR="${BOTPARTY_INSTALL_SOURCE_DIR:-/opt/botparty/source}"
ALLOWED_SIGNERS="${BOTPARTY_INSTALL_ALLOWED_SIGNERS:-}"
BUNDLE_URL="${BOTPARTY_INSTALL_BUNDLE_URL:-}"
BUNDLE_SHA256="${BOTPARTY_INSTALL_BUNDLE_SHA256:-}"

# Release builds replace these markers. Environment variables remain available for
# private channels and for exercising the source template in installer tests.
if [[ "$EMBEDDED_REF" != "$UNSET_REF_MARKER" ]]; then
  REF="${REF:-$EMBEDDED_REF}"
  case "$(uname -m)" in
    x86_64 | amd64) release_arch="amd64" ;;
    aarch64 | arm64) release_arch="arm64" ;;
    armv7l | armv7) release_arch="armv7" ;;
    *) echo "This release has no bundle for $(uname -m)" >&2; exit 1 ;;
  esac
  release_bundle="$({ printf '%s' "$EMBEDDED_BUNDLES" | base64 --decode; } \
    | awk -F '\t' -v arch="$release_arch" '$1 == arch { print $2 "\t" $3 }')"
  if [[ -z "$release_bundle" ]]; then
    echo "This release has no bundle for $release_arch" >&2
    exit 1
  fi
  IFS=$'\t' read -r embedded_bundle_url embedded_bundle_sha256 <<<"$release_bundle"
  BUNDLE_URL="${BUNDLE_URL:-$embedded_bundle_url}"
  BUNDLE_SHA256="${BUNDLE_SHA256:-$embedded_bundle_sha256}"
fi

temporary_allowed_signers=""
cleanup() {
  [[ -z "$temporary_allowed_signers" ]] || rm -f "$temporary_allowed_signers"
  [[ -z "${bundle_directory:-}" ]] || rm -rf "$bundle_directory"
}
trap cleanup EXIT

if [[ -z "$ALLOWED_SIGNERS" \
  && "$EMBEDDED_ALLOWED_SIGNERS" != "$UNSET_ALLOWED_SIGNERS_MARKER" ]]; then
  temporary_allowed_signers="$(mktemp)"
  chmod 0600 "$temporary_allowed_signers"
  printf '%s' "$EMBEDDED_ALLOWED_SIGNERS" | base64 --decode >"$temporary_allowed_signers"
  ALLOWED_SIGNERS="$temporary_allowed_signers"
fi

case "$REPOSITORY" in
  https://*) ;;
  *) echo "BOTPARTY_INSTALL_REPOSITORY must use HTTPS" >&2; exit 1 ;;
esac
if [[ ! "$REF" =~ ^[0-9a-fA-F]{40}$ && ! "$REF" =~ ^[0-9a-fA-F]{64}$ ]]; then
  echo "BOTPARTY_INSTALL_REF must be a full immutable commit id" >&2
  exit 1
fi
REF="${REF,,}"
if [[ "$SOURCE_DIR" != /* ]] || [[ "$SOURCE_DIR" == "/" ]]; then
  echo "BOTPARTY_INSTALL_SOURCE_DIR must be an absolute non-root path" >&2
  exit 1
fi
if [[ -z "$ALLOWED_SIGNERS" ]]; then
  echo "A trusted BOTPARTY_INSTALL_ALLOWED_SIGNERS file is required" >&2
  exit 1
fi
if [[ "$ALLOWED_SIGNERS" != /* ]] || [[ ! -f "$ALLOWED_SIGNERS" ]] || [[ -L "$ALLOWED_SIGNERS" ]]; then
  echo "BOTPARTY_INSTALL_ALLOWED_SIGNERS must name a regular absolute file" >&2
  exit 1
fi
if [[ ! "$BUNDLE_URL" =~ ^https:// ]] || [[ ! "$BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
  echo "BOTPARTY_INSTALL_BUNDLE_URL and its lowercase SHA-256 digest are required" >&2
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
if ! command -v python3 >/dev/null 2>&1; then
  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y python3
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
else
  "${SUDO[@]}" git init "$SOURCE_DIR"
  "${SUDO[@]}" git -C "$SOURCE_DIR" remote add origin "$REPOSITORY"
  "${SUDO[@]}" git -C "$SOURCE_DIR" fetch --depth 1 origin "$REF"
fi
"${SUDO[@]}" git -C "$SOURCE_DIR" checkout --detach FETCH_HEAD
actual_commit="$("${SUDO[@]}" git -C "$SOURCE_DIR" rev-parse HEAD)"
if [[ "$actual_commit" != "$REF" ]]; then
  echo "Fetched commit does not match BOTPARTY_INSTALL_REF" >&2
  exit 1
fi
"${SUDO[@]}" git -C "$SOURCE_DIR" \
  -c gpg.format=ssh \
  -c "gpg.ssh.allowedSignersFile=$ALLOWED_SIGNERS" \
  verify-commit "$REF"
bundle_directory="$(mktemp -d)"
bundle_path="$bundle_directory/install-bundle.zip"
BUNDLE_URL="$BUNDLE_URL" BUNDLE_PATH="$bundle_path" python3 - <<'PY'
import os
import urllib.request

request = urllib.request.Request(
    os.environ["BUNDLE_URL"], headers={"User-Agent": "botparty-bootstrap/1"}
)
with urllib.request.urlopen(request, timeout=60) as response:
    if response.geturl().split(":", 1)[0].lower() != "https":
        raise SystemExit("install bundle redirect did not remain on HTTPS")
    payload = response.read(256 * 1024 * 1024 + 1)
if len(payload) > 256 * 1024 * 1024:
    raise SystemExit("install bundle exceeds 256 MiB")
with open(os.environ["BUNDLE_PATH"], "wb") as handle:
    handle.write(payload)
PY
actual_bundle_sha256="$(sha256sum "$bundle_path" | awk '{print $1}')"
if [[ "$actual_bundle_sha256" != "$BUNDLE_SHA256" ]]; then
  echo "Install bundle digest does not match" >&2
  exit 1
fi
extract_directory="$bundle_directory/extracted"
BUNDLE_PATH="$bundle_path" EXTRACT_DIRECTORY="$extract_directory" python3 - <<'PY'
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath

target = Path(os.environ["EXTRACT_DIRECTORY"])
target.mkdir(mode=0o700)
with zipfile.ZipFile(os.environ["BUNDLE_PATH"]) as archive:
    total = 0
    for info in archive.infolist():
        path = PurePosixPath(info.filename)
        mode = info.external_attr >> 16
        if (
            path.is_absolute()
            or ".." in path.parts
            or not path.parts
            or path.parts[0]
            not in {"requirements.txt", "installer-requirements.txt", "wheelhouse"}
            or stat.S_ISLNK(mode)
            or info.is_dir()
        ):
            raise SystemExit("install bundle contains an unsafe entry")
        total += info.file_size
        if total > 512 * 1024 * 1024:
            raise SystemExit("expanded install bundle exceeds 512 MiB")
        destination = target.joinpath(*path.parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with archive.open(info) as source, destination.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
PY
mapfile -t release_wheels < <(find "$extract_directory/wheelhouse" -maxdepth 1 -type f \
  -name 'botparty_robot-*.whl' -print)
if [[ ${#release_wheels[@]} -ne 1 ]]; then
  echo "Install bundle must contain exactly one BotParty release wheel" >&2
  exit 1
fi
wheel_sha256="$(sha256sum "${release_wheels[0]}" | awk '{print $1}')"

"${SUDO[@]}" env \
  BOTPARTY_VERIFIED_SOURCE_COMMIT="$REF" \
  BOTPARTY_INSTALL_ALLOWED_SIGNERS="$ALLOWED_SIGNERS" \
  BOTPARTY_VERIFIED_WHEEL_SHA256="$wheel_sha256" \
  "$SOURCE_DIR/scripts/install-botparty-client.sh" \
  --release-wheel "${release_wheels[0]}" \
  --offline-wheelhouse "$extract_directory/wheelhouse" \
  "$@"
