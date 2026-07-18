#!/usr/bin/env bash
set -euo pipefail

test_root="$(mktemp -d)"
trap 'rm -rf "$test_root"' EXIT
fake_bin="$test_root/bin"
mkdir -p "$fake_bin"

# The integration suite exercises the root-side transaction and generated unit without
# downloading Python packages. The shim models only the public commands consumed by the installer.
cat >"$fake_bin/python3" <<'PYTHON'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
  target="${3:?venv target missing}"
  mkdir -p "$target/bin"
  cp "$0" "$target/bin/python"
  cat >"$target/bin/pip" <<'PIP'
#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-r" ]]; then
    requirement="${2:?requirement file missing}"
    if grep -Eq '^\+' "$requirement"; then
      echo 'invalid leading patch marker in requirement' >&2
      exit 65
    fi
    grep -Eq -- '--hash=sha256:[a-f0-9]{64}' "$requirement"
    shift 2
    continue
  fi
  shift
done
exit 0
PIP
  cat >"$target/bin/botparty-robot" <<'ROBOT'
#!/usr/bin/env bash
if [[ " $* " == *" device-policy "* ]]; then
  printf '%s\n' 'DeviceAllow=/dev/null rw'
else
  printf '%s\n' 'botparty-robot 0.2.0 (installer-test)'
fi
ROBOT
  chmod 0755 "$target/bin/python" "$target/bin/pip" "$target/bin/botparty-robot"
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" && "${3:-}" == "uninstall" ]]; then
  rm -f "$(dirname "$0")/pip" "$(dirname "$0")/pip3"
  exit 0
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "build" ]]; then
  if [[ "${BOTPARTY_TEST_FAIL_BUILD:-0}" == "1" ]]; then
    printf '%s\n' 'injected wheel build failure' >&2
    exit 42
  fi
  output=""
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--outdir" ]]; then
      output="${2:?outdir missing}"
      break
    fi
    shift
  done
  mkdir -p "$output"
  printf '%s\n' 'deterministic installer integration wheel' \
    >"$output/botparty_robot-0.2.0-py3-none-any.whl"
  exit 0
fi
exit 0
PYTHON
chmod 0755 "$fake_bin/python3"

cat >"$fake_bin/systemctl" <<'SYSTEMCTL'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>/tmp/botparty-systemctl.log
SYSTEMCTL
chmod 0755 "$fake_bin/systemctl"

export PATH="$fake_bin:/usr/sbin:/usr/bin:/sbin:/bin"
export BOTPARTY_CLAIM_TOKEN="installer-test-claim-token"
export BOTPARTY_VERIFIED_SOURCE_COMMIT="0000000000000000000000000000000000000000"
wheelhouse="$test_root/wheelhouse"
mkdir -p "$wheelhouse"
release_wheel="$wheelhouse/botparty_robot-0.2.0-py3-none-any.whl"
printf '%s\n' 'deterministic attested installer integration wheel' >"$release_wheel"
export BOTPARTY_VERIFIED_WHEEL_SHA256
BOTPARTY_VERIFIED_WHEEL_SHA256="$(sha256sum "$release_wheel" | awk '{print $1}')"

installer=(bash scripts/install-botparty-client.sh --no-apt --no-streamer \
  --release-wheel "$release_wheel" --offline-wheelhouse "$wheelhouse")

# Default production layout and hardened unit.
"${installer[@]}"
test -x /opt/botparty/venv/bin/botparty-robot
test ! -e /opt/botparty/venv/bin/pip
test -x /opt/botparty/botparty-service-launcher.sh
test -f /etc/botparty/config.yaml
test -d /var/lib/botparty
test "$(stat -c '%a' /var/lib/botparty)" = "700"
test "$(stat -c '%a' /etc/botparty/config.yaml)" = "640"
test "$(stat -c '%U' /var/lib/botparty)" = "botparty"
test "$(stat -c '%U' /etc/botparty/config.yaml)" = "root"
grep -Fqx 'daemon-reload' /tmp/botparty-systemctl.log
grep -Fqx 'enable botparty-robot.service' /tmp/botparty-systemctl.log
systemd-analyze verify /etc/systemd/system/botparty-robot.service
grep -Fqx 'NoNewPrivileges=yes' /etc/systemd/system/botparty-robot.service
grep -Fqx 'ProtectSystem=strict' /etc/systemd/system/botparty-robot.service
grep -Fqx 'DevicePolicy=closed' /etc/systemd/system/botparty-robot.service
grep -Fqx 'KillMode=mixed' /etc/systemd/system/botparty-robot.service
grep -Fqx 'ProtectProc=invisible' /etc/systemd/system/botparty-robot.service
grep -Fqx 'MemoryMax=1536M' /etc/systemd/system/botparty-robot.service
grep -Fqx 'TasksMax=256' /etc/systemd/system/botparty-robot.service
grep -Fqx 'OOMPolicy=stop' /etc/systemd/system/botparty-robot.service
grep -Fqx 'Environment=BOTPARTY_DEPLOYMENT_MODE=production' /etc/systemd/system/botparty-robot.service

# A second run is an upgrade: the active environment is swapped and one rollback is retained.
"${installer[@]}"
test -x /opt/botparty/venv/bin/python
test "$(find /opt/botparty -maxdepth 1 -type d -name 'venv.rollback.*' | wc -l)" = "1"
test ! -e /opt/botparty/venv.new

# Every activation boundary rolls back to the exact previously active wheel metadata.
baseline_hash="$(sha256sum /opt/botparty/installed-wheel.sha256 | awk '{print $1}')"
for failure_point in venv streamer launcher metadata config unit; do
  if BOTPARTY_TEST_FAIL_AT="$failure_point" "${installer[@]}" >/tmp/activation-failure.log 2>&1; then
    echo "injected activation failure unexpectedly succeeded at $failure_point" >&2
    exit 1
  fi
  grep -Fq "injected activation failure at $failure_point" /tmp/activation-failure.log
  test -x /opt/botparty/venv/bin/botparty-robot
  test "$(sha256sum /opt/botparty/installed-wheel.sha256 | awk '{print $1}')" = "$baseline_hash"
done

# TERM at every activation boundary follows the same rollback and cleanup transaction.
for signal_point in venv streamer launcher metadata config unit; do
  if BOTPARTY_TEST_SIGNAL_AT="$signal_point" "${installer[@]}" >/tmp/activation-signal.log 2>&1; then
    echo "injected TERM unexpectedly succeeded at $signal_point" >&2
    exit 1
  fi
  grep -Fq "injected termination signal at $signal_point" /tmp/activation-signal.log
  grep -Fq 'the previous release was restored' /tmp/activation-signal.log
  test -x /opt/botparty/venv/bin/botparty-robot
  test "$(sha256sum /opt/botparty/installed-wheel.sha256 | awk '{print $1}')" = "$baseline_hash"
  test -z "$(find /opt/botparty -maxdepth 1 -type d -name '.install-staging.*' -print -quit)"
done

# Custom safe paths are accepted without touching them in dry-run mode.
custom="$test_root/custom"
custom_output="$("${installer[@]}" --dry-run --no-service \
  --prefix "$custom/opt/client" \
  --config "$custom/etc/config.yaml" \
  --state-dir "$custom/var/state")"
grep -Fq "Prefix: $custom/opt/client" <<<"$custom_output"
test ! -e "$custom/opt/client"

# Unsafe whitespace and symlinked path components are rejected before mutation.
if "${installer[@]}" --dry-run --no-service \
  --prefix "$test_root/path with spaces" >/tmp/unsafe-space.log 2>&1; then
  echo 'whitespace path was unexpectedly accepted' >&2
  exit 1
fi
grep -Fq 'contain no whitespace' /tmp/unsafe-space.log
real_parent="$test_root/real-parent"
mkdir -p "$real_parent"
ln -s "$real_parent" "$test_root/link-parent"
if "${installer[@]}" --dry-run --no-service \
  --prefix "$test_root/link-parent/client" >/tmp/unsafe-symlink.log 2>&1; then
  echo 'symlinked path component was unexpectedly accepted' >&2
  exit 1
fi
grep -Fq 'must not contain symlink components' /tmp/unsafe-symlink.log

# Account and group policy fail before filesystem mutation.
if "${installer[@]}" --dry-run --user root --reuse-user >/tmp/wrong-user.log 2>&1; then
  echo 'root service account was unexpectedly accepted' >&2
  exit 1
fi
grep -Fq 'Refusing to install the service as root' /tmp/wrong-user.log
if "${installer[@]}" --dry-run --device-groups definitely-missing >/tmp/missing-group.log 2>&1; then
  echo 'missing device group was unexpectedly accepted' >&2
  exit 1
fi
grep -Fq 'Requested device group does not exist' /tmp/missing-group.log

# A mismatched attested wheel never replaces or leaves a candidate environment.
failed="$test_root/failed"
if BOTPARTY_VERIFIED_WHEEL_SHA256="$(printf '0%.0s' {1..64})" "${installer[@]}" --no-service \
  --prefix "$failed/opt/client" \
  --config "$failed/etc/config.yaml" \
  --state-dir "$failed/var/state" >/tmp/failed-build.log 2>&1; then
  echo 'mismatched release wheel unexpectedly succeeded' >&2
  exit 1
fi
grep -Fq 'Attested release wheel digest does not match' /tmp/failed-build.log
test ! -e "$failed/opt/client/venv"
test ! -e "$failed/opt/client/venv.new"

printf '%s\n' 'installer integration matrix passed'
