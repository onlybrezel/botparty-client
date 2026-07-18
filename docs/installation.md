# Installation and recovery

## Supported host

- Linux on amd64, arm64 or armv7
- Python 3.10 or newer
- systemd for the production service profile

Install only the extras required by the configured profile. The default core supports `hardware:none`, FFmpeg video and disabled TTS. Hashed installer profiles are `vision`, `hardware`, `gpio`, `serial`, `mqtt`, `usb`, `telemetry`, `polly` and `google-tts`. `hardware` combines GPIO, serial, MQTT and USB when a device needs several buses.

## Automatic production install

Obtain the commit ID and SSH signer identity from a verified signed release. Provision the
operator-controlled allowed-signers file, download the versioned bootstrap release artifact and
verify its build attestation before running it.

```bash
gh release download v0.2.0 --repo onlybrezel/botparty-client \
  --pattern bootstrap-install.sh --pattern SHA256SUMS \
  --pattern 'botparty-robot-0.2.0-linux-amd64.zip' --dir /tmp/botparty-release
gh attestation verify /tmp/botparty-release/bootstrap-install.sh --repo onlybrezel/botparty-client
gh attestation verify /tmp/botparty-release/botparty-robot-0.2.0-linux-amd64.zip \
  --repo onlybrezel/botparty-client
BOTPARTY_BUNDLE_SHA256="$(grep 'botparty-robot-0.2.0-linux-amd64.zip$' \
  /tmp/botparty-release/SHA256SUMS | cut -d' ' -f1)"
BOTPARTY_RELEASE_COMMIT="<verified-40-character-release-commit>"
sudo env BOTPARTY_INSTALL_REF="$BOTPARTY_RELEASE_COMMIT" \
  BOTPARTY_INSTALL_ALLOWED_SIGNERS=/etc/botparty/release-allowed-signers \
  BOTPARTY_INSTALL_BUNDLE_URL=https://github.com/onlybrezel/botparty-client/releases/download/v0.2.0/botparty-robot-0.2.0-linux-amd64.zip \
  BOTPARTY_INSTALL_BUNDLE_SHA256="$BOTPARTY_BUNDLE_SHA256" \
  bash /tmp/botparty-release/bootstrap-install.sh --device-groups video
sudoedit /etc/botparty/config.yaml
sudo -u botparty /opt/botparty/venv/bin/botparty-robot \
  --config /etc/botparty/config.yaml doctor
sudo systemctl enable --now botparty-robot.service
```

The bootstrap accepts only a full immutable commit ID. It verifies that exact commit against the
operator-controlled SSH allowed-signers file, checks it out detached at `/opt/botparty/source` and
runs the installer. Unsigned commits require the explicit development-only
workflow through an unprivileged local virtual environment; the root bootstrap has no unsigned
override. An existing checkout is reused only when its origin matches and it has no local changes.
The bundle SHA-256 and GitHub attestation bind the
architecture-specific offline wheelhouse; package installation uses `--no-index` and
`--require-hashes`.

## Production source policy

Production hosts use the attested bootstrap path above. Direct execution of a checkout as root is
not supported. The inner installer rejects production runs without the immutable commit evidence
provided by the bootstrap. `--development-install` is limited to a non-service, non-streamer local
installation; normal development should use the virtual environment below.

The installer:

- installs the attested release wheel and dependencies without contacting a package index;
- pins the temporary installer frontend from the wheelhouse and removes it before activation;
- downloads and verifies the correct video streamer for the host architecture;
- runs the service as the dedicated `botparty` user;
- grants only explicitly selected device groups;
- stores config in `/etc/botparty` and state in `/var/lib/botparty`;
- installs a systemd unit with readiness, watchdog and shutdown limits;
- keeps the previous environment for rollback.

The cloned repository is only the installation source. The service runs from the installed wheel
in `/opt/botparty/venv`, not from the checkout.

CI executes the root-side transaction in a disposable Ubuntu container. The matrix covers the
default and custom layouts, a repeated upgrade, account/group policy failures, an interrupted wheel
build, ownership and modes, retained rollback state and `systemd-analyze verify` for the hardened
unit. No installer integration test writes to the CI host filesystem.

Add groups only for devices in use. Production extras require a release bundle whose wheelhouse
contains the matching locked profile; the installer fails offline when an artifact is absent.

## Signed streamer

The production installer downloads the official streamer automatically. Its built-in release
catalog pins architecture, URL, byte length and SHA-256. The installer additionally checks the
ELF format and reported version before atomic activation. A failed verification aborts the
installation and preserves the previous binary. Client releases update this catalog together
with the tested streamer version.

The standalone command uses the same default:

```bash
sudo BOTPARTY_PYTHON=/opt/botparty/venv/bin/python \
  BOTPARTY_STREAMER_DIR=/opt/botparty/libexec \
  BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID=0 \
  ./scripts/install-botparty-streamer.sh
```

Private release channels may pass `--manifest-url` and `--public-key`; both are required and the
manifest must have a valid Ed25519 signature. Use `--no-streamer` on the main installer only for
an intentionally headless robot.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements/build-toolchain.txt
.venv/bin/pip install --require-hashes -r requirements/dev.txt
.venv/bin/pip install --no-deps -e .
cp config.example.yaml config.yaml
chmod 600 config.yaml
.venv/bin/botparty-robot --config config.yaml config validate
```

Plain HTTP and WebSocket connections work only on loopback and only with `server.allow_insecure_dev_transport: true`.

## Service checks

```bash
systemctl status botparty-robot.service
journalctl -u botparty-robot.service -f
curl --fail http://127.0.0.1:9100/live
curl --fail http://127.0.0.1:9100/ready
```

`/live` proves that the process and supervisor are alive. `/ready` additionally requires authentication, control connectivity, an unlatched safety state and required media.

## Recovery

1. Stop the service. This latches the local safety controller and de-energizes the adapter.
2. Run `doctor` and inspect `/health`.
3. For an unconfirmed OTA, run the launcher normally; it detects the boot-attempt marker and
   restores the previous slot. For a normal installer upgrade, stop the service and run
   `sudo /opt/botparty/botparty-service-launcher.sh --rollback-installer` once. The command validates
   both environments and atomically exchanges `venv` and `venv.previous`.
4. Validate config before starting the service.
5. Perform a supervised stop/reset test before restoring public control.

OTA activation is confirmed only after control readiness and, when required, a live media frame. A pending release that fails its first boot is rolled back by the launcher.
