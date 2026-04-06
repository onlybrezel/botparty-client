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
- Python 3.11+
- Camera device (`/dev/video0` or compatible)
- Network connection

## Quick Start

```bash
# 1) Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2) Install dependencies
pip install -r requirements.txt

# 3) Create config
cp config.example.yaml config.yaml

# 4) Run
python -m botparty_robot
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
- [Hardware adapters](docs/hardware/index.md)
- [Video profiles](docs/video/index.md)
- [TTS profiles](docs/tts/index.md)
- [Troubleshooting](docs/troubleshooting.md)

## Typical next setup steps

1. Pick your hardware adapter in `config.yaml`.
2. Pick your video profile (`ffmpeg` recommended on Raspberry Pi).
3. Tune camera resolution/FPS for stable low latency.
4. Enable TTS only if your audio output is configured.

## Notes

- Keep your `claim_token` secret.
- For optional adapter/profile dependencies, see the specific docs pages above.
