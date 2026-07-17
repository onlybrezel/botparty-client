# Installation and recovery

## Supported host

- Linux on amd64, arm64 or armv7
- Python 3.10 or newer
- systemd for the production service profile

Install only the extras required by the configured profile. The default core supports `hardware:none`, FFmpeg video and disabled TTS. Hashed installer profiles are `vision`, `hardware`, `gpio`, `serial`, `mqtt`, `usb`, `telemetry`, `polly` and `google-tts`. `hardware` combines GPIO, serial, MQTT and USB when a device needs several buses.

## Production install

```bash
sudo apt-get update
sudo apt-get install -y git
git clone --depth 1 https://github.com/onlybrezel/botparty-client.git
cd botparty-client
sudo ./scripts/install-botparty-client.sh --device-groups video
sudoedit /etc/botparty/config.yaml
sudo -u botparty /opt/botparty/venv/bin/botparty-robot \
  --config /etc/botparty/config.yaml doctor
sudo systemctl enable --now botparty-robot.service
```

The installer:

- builds and installs a wheel rather than an editable checkout;
- downloads and verifies the correct video streamer for the host architecture;
- runs the service as the dedicated `botparty` user;
- grants only explicitly selected device groups;
- stores config in `/etc/botparty` and state in `/var/lib/botparty`;
- installs a systemd unit with readiness, watchdog and shutdown limits;
- keeps the previous environment for rollback.

The cloned repository is only the source for the first installation or a deliberate manual
reinstall. The service does not run from the checkout. An OTA request from the BotParty server
downloads a signed release into the inactive slot, validates it and switches slots atomically;
it never runs `git pull` against a live robot process.

Add groups only for devices in use:

```bash
sudo ./scripts/install-botparty-client.sh \
  --extras hardware --device-groups video,gpio,dialout
```

## Client updates

Automatic updates are started by an authorized `update_client` action from the BotParty server.
The client downloads the signed release, installs it into the inactive A/B slot and restarts only
after verification. A failed readiness check restores the previous slot.

For a manual update, pull the installation checkout with fast-forward protection and rerun the
installer with the same extras and device groups used for the first installation:

```bash
cd botparty-client
git pull --ff-only
sudo systemctl stop botparty-robot.service
sudo ./scripts/install-botparty-client.sh --device-groups video
sudo systemctl start botparty-robot.service
```

The installer validates the new environment before replacing `/opt/botparty/venv`, keeps the
previous environment at `/opt/botparty/venv.previous` and preserves the existing configuration
and device identity.

## Signed streamer

The production installer downloads the official streamer automatically. Its built-in release
catalog pins architecture, URL, byte length and SHA-256. The installer additionally checks the
ELF format and reported version before atomic activation. A failed verification aborts the
installation and preserves the previous binary. Client releases update this catalog together
with the tested streamer version.

The standalone command uses the same default:

```bash
sudo -u botparty BOTPARTY_PYTHON=/opt/botparty/venv/bin/python \
  BOTPARTY_STREAMER_DIR=/var/lib/botparty/bin \
  ./scripts/install-botparty-streamer.sh
```

Private release channels may pass `--manifest-url` and `--public-key`; both are required and the
manifest must have a valid Ed25519 signature. Use `--no-streamer` on the main installer only for
an intentionally headless robot.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev,vision]'
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
3. Restore the previous A/B slot with the service launcher or restore an encrypted device backup.
4. Validate config before starting the service.
5. Perform a supervised stop/reset test before restoring public control.

OTA activation is confirmed only after control readiness and, when required, a live media frame. A pending release that fails its first boot is rolled back by the launcher.
