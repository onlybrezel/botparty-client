# BotParty Robot Client

Python client that runs on your robot and connects it to BotParty.

The README is intentionally short. Full documentation is in [docs/index.md](docs/index.md).

## What this client does

- Connects your robot to the BotParty API using a claim token
- Opens the control channel and receives movement commands
- Publishes camera video
- Optionally speaks chat messages via TTS

## Requirements

- Linux machine (Raspberry Pi, Jetson, or Ubuntu/Debian)
- Python 3.10+
- Camera device (`/dev/video0` or compatible)
- Network connection

## Quick Start

One-command bootstrap (install deps + venv + optional service + streamer helper):

```bash
./scripts/install-botparty-client.sh
```

Then edit `config.yaml` and check logs with:

```bash
sudo journalctl -u botparty-robot.service -f
```

Manual start (no `source .venv/bin/activate` needed):

```bash
./scripts/start-botparty-robot.sh
```

```bash
# 0) Install base packages first
sudo apt update
sudo apt install -y git python3-pip python3-venv ffmpeg

# 1) Clone the repo and create a virtual environment
git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client

python3 -m venv .venv
source .venv/bin/activate

# 2) Install dependencies
pip install -e ".[all]"

# 3) Create config
cp config.example.yaml config.yaml
chmod 600 config.yaml  # claim_token is sensitive — restrict file permissions

# 4) Run
python -m botparty_robot
```

Without `venv`, you can also install and run it directly with system Python:

```bash
git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client
python3 -m pip install --break-system-packages -e ".[all]"
cp config.example.yaml config.yaml
chmod 600 config.yaml  # claim_token is sensitive — restrict file permissions
python3 -m botparty_robot
```

## Minimal config

Edit `config.yaml` and set at least:

```yaml
server:
  api_url: "https://botparty.live"
  livekit_url: "wss://botparty.live/rtc"
  claim_token: "YOUR_CLAIM_TOKEN"

video:
  type: "ffmpeg"
  options: {}

hardware:
  type: "none"
  options: {}
```

Then switch `hardware.type` to your real adapter (for example `l298n`) once basic connectivity works.

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

## Typical next setup steps

1. Pick your hardware adapter in `config.yaml`.
2. Pick your video profile (`ffmpeg` is the easiest default).
3. Tune camera resolution/FPS for stable low latency.
4. Enable TTS only if your audio output is configured.

## Notes

- Keep your `claim_token` secret.

- `botparty-streamer` is our selfmade video transmitter for maximum performance, low CPU usage and low latency.
- On Raspberry Pi OS Bookworm, `libatlas-base-dev` is not needed for the normal install path and may not exist anymore.
- If `sudo apt install python3-rpi.gpio` wants to remove `python3-rpi-lgpio`, that is usually expected for BotParty's built-in GPIO adapters.
- `venv` is the recommended default, but a no-`venv` system-Python install is also supported and can be convenient for GPIO-heavy Raspberry Pi setups.
- For multi-camera robots, prefer stable camera device symlinks from `/dev/v4l/by-id/` or `/dev/v4l/by-path/` instead of `/dev/video0` and `/dev/video2`.
- For optional adapter/profile dependencies, see the specific docs pages above.
