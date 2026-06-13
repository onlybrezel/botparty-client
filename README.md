# BotParty Robot Client

Python client that runs on your robot and connects it to BotParty.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-blue.svg)

The README is intentionally short. Full documentation is in [docs/index.md](docs/index.md).

## Get it running

One-command bootstrap (venv, deps, optional `botparty-streamer`, and systemd service):

```bash
./scripts/install-botparty-client.sh
```

Edit `config.yaml`, then follow logs:

```bash
sudo journalctl -u botparty-robot.service -f
```

Manual run (no venv activation needed):

```bash
./scripts/start-botparty-robot.sh
```

For the full apt + clone + venv steps and the system-Python install path (useful on some Pi images), see [Installation](docs/installation.md).

## What this client does

- Connects using a claim token from the BotParty dashboard
- Receives drive commands over the control channel and forwards them to your hardware adapter
- Publishes live camera video (ffmpeg + botparty-streamer by default)
- Optionally speaks chat messages via a TTS profile

## Requirements

- Linux (Raspberry Pi, Jetson, Ubuntu/Debian, ...)
- Python 3.10+
- Camera device (`/dev/video0`, libcamera, or compatible)
- Network connection (wired preferred)

## Minimal config

```bash
cp config.example.yaml config.yaml
chmod 600 config.yaml
```

Edit at least the claim token:

```yaml
server:
  api_url: "https://botparty.live"
  livekit_url: "wss://botparty.live"
  claim_token: "PASTE_YOUR_CLAIM_TOKEN_HERE"

video:
  type: "ffmpeg"
  options: {}

hardware:
  type: "none"
  options: {}
```

Start with `hardware.type: none` for first connection tests, then switch it to your board (e.g. `l298n`).

You can also pass the token via the `BOTPARTY_CLAIM_TOKEN` environment variable. This is handy for systemd units and secret managers.

## Documentation

- [Docs home](docs/index.md)
- [Installation](docs/installation.md)
- [Configuration reference](docs/configuration.md)
- [Client mixins architecture](docs/client-mixins.md)
- [Multi-camera](docs/multi-camera.md)
- [Hardware adapters](docs/hardware/index.md)
- [Video profiles](docs/video/index.md)
- [TTS profiles](docs/tts/index.md)
- [Troubleshooting](docs/troubleshooting.md)

## Notes

- Keep your `claim_token` secret.
- Local health endpoint: `http://127.0.0.1:9100/health` (override with `BOTPARTY_HEALTH_*` vars or disable via `BOTPARTY_HEALTH_ENABLED=false`).
- `botparty-streamer` is the self-made video transmitter for max performance, low CPU and low latency.
- On Raspberry Pi OS Bookworm `libatlas-base-dev` is usually not required anymore.
- `sudo apt install python3-rpi.gpio` may want to remove `python3-rpi-lgpio` — this is expected for the built-in GPIO adapters.
- `venv` is the safer default, but a direct system-Python install (`--break-system-packages`) is supported and sometimes more convenient for GPIO work.
- Multi-camera setups: use stable symlinks from `/dev/v4l/by-id/` or `/dev/v4l/by-path/` instead of plain `/dev/video0`.
- Extra dependencies for specific hardware or TTS engines are listed in the docs linked above.
