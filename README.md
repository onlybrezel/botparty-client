# BotParty Robot Client

BotParty's Python robot client connects a camera, control adapter and optional text-to-speech engine to the BotParty platform.

## Install

The installer builds an immutable wheel, downloads the verified video streamer, creates the
`botparty` service user and installs a hardened systemd service:

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --depth 1 https://github.com/onlybrezel/botparty-client.git
cd botparty-client
sudo ./scripts/install-botparty-client.sh --device-groups video
sudoedit /etc/botparty/config.yaml
sudo systemctl enable --now botparty-robot.service
```

The checkout is used only as the installation source. The installed service runs from
`/opt/botparty`; server-triggered updates download a fresh signed release into an A/B slot and do
not modify the checkout with `git pull`.

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
.venv/bin/pip install -e '.[dev]'
.venv/bin/botparty-robot --config config.example.yaml config validate
```

## Safety contract

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
sudo -u botparty BOTPARTY_PYTHON=/opt/botparty/venv/bin/python \
  BOTPARTY_STREAMER_DIR=/var/lib/botparty/bin \
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
botparty-robot --config config.yaml backup generate-key --key backup.key
```

## Documentation

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
