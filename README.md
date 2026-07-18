# BotParty Robot Client

BotParty's Python robot client connects a camera, control adapter and optional text-to-speech engine to the BotParty platform.

## Install

Download the versioned bootstrap artifact and verify its GitHub build attestation before running it.
Store the trusted release SSH principals in `/etc/botparty/release-allowed-signers` and use the
40-character commit ID named by the verified release:

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
sudo systemctl enable --now botparty-robot.service
```

Production installation is supported only through the verified bootstrap above. The bootstrap keeps
its immutable checkout at `/opt/botparty/source`; the installed service runs from
`/opt/botparty/venv`. For local source development, use the unprivileged virtual-environment setup
below instead of installing a system service.

Start with `hardware.type: none`. Validate the host before enabling motors:

```bash
sudo -u botparty /opt/botparty/venv/bin/botparty-robot \
  --config /etc/botparty/config.yaml config validate
sudo -u botparty /opt/botparty/venv/bin/botparty-robot \
  --config /etc/botparty/config.yaml doctor
```

For a local development environment:

```bash
python3 -m venv .venv
.venv/bin/pip install --require-hashes --no-deps -r requirements/build-toolchain.txt
.venv/bin/pip install --require-hashes -r requirements/dev.txt
.venv/bin/pip install --no-deps -e .
.venv/bin/botparty-robot --config config.example.yaml config validate
```

## Safety contract

The current release profile is **media-only**. `hardware.type: none` is the only released hardware
profile; moving adapters remain blocked until release-specific HIL evidence exists.

- Every control-plane disconnect, watchdog expiry, update and shutdown latches a local stop.
- A latched robot accepts no movement until an authorized `reset_safety` action with `safety:reset` scope arrives.
- Stop bypasses the normal command queue. Old command epochs cannot perform later actuator writes.
- Movement is rejected while media is degraded by default. Stop remains available.
- Community adapters require a hardware-in-the-loop stop-latency check before production use.

The local endpoints are `http://127.0.0.1:9100/live`, `/ready` and `/health`. Non-loopback binding requires `BOTPARTY_HEALTH_AUTH_TOKEN_FILE`.

## Signed components

The main installer downloads the official streamer automatically. The bundled release catalog
pins the exact URL, architecture, size and SHA-256; ELF and version checks run before atomic
activation. The standalone installer uses the same verified default:

```bash
sudo BOTPARTY_PYTHON=/opt/botparty/venv/bin/python \
  BOTPARTY_STREAMER_DIR=/opt/botparty/libexec \
  BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID=0 \
  ./scripts/install-botparty-streamer.sh
```

Private release channels can override the default with an Ed25519-signed manifest and pinned
public key. `--no-streamer` is available only for intentionally headless installations.

OTA updates are triggered by an authorized BotParty server action and use signed, immutable A/B
release bundles. The standard channel is prefilled but disabled until its public key is installed;
failed readiness rolls back to the previous slot.

## Operator commands

```bash
botparty-robot --config config.yaml config validate
botparty-robot --config config.yaml doctor --network
botparty-robot --config config.yaml support-bundle --output support.zip
botparty-robot setup --output config.yaml
botparty-robot --config config.yaml config export --output config.redacted.yaml
botparty-robot backup generate-key --key-file backup.key
```

## Documentation

- [Developer guide](DEVELOPER_GUIDE.md)
- [Installation and recovery](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Security and threat model](docs/security.md)
- [Operations, SLOs and runbooks](docs/operations.md)
- [Release and OTA process](docs/release.md)
- [Privacy, data flows and retention](docs/privacy.md)
- [Adapter support matrix](docs/adapter-support.md)
- [Performance budgets](docs/performance.md)
- [Backup and restore](docs/backup.md)
- [Protocol](docs/protocol.md)

## Support and security

Use [SUPPORT.md](SUPPORT.md) for support requests and [SECURITY.md](SECURITY.md) for private vulnerability reports. The project is licensed under the [MIT License](LICENSE).
