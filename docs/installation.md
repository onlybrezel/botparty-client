# Installation

## Requirements

| Requirement | Version |
|-------------|---------|
| Python | 3.10 or newer |
| pip | latest |
| Network | Stable internet connection (wired preferred for robots) |

---

## Raspberry Pi (recommended)

Tested on Raspberry Pi 4B and Pi 5 running Raspberry Pi OS Bookworm (64-bit).

### 1. System dependencies

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv ffmpeg
```

For audio / TTS:

```bash
sudo apt install -y alsa-utils espeak mpg123
```

For GPIO access (L298N, MDD10, MotoZero and other `RPi.GPIO`-based adapters):

```bash
sudo apt install -y python3-rpi.gpio
sudo usermod -aG gpio $USER   # log out and back in after this
```

If `apt` wants to remove `python3-rpi-lgpio`, that is expected on Raspberry Pi OS. The two packages provide overlapping GPIO compatibility layers and conflict with each other. For the built-in BotParty GPIO adapters, `python3-rpi.gpio` is the documented and supported choice.

### 2. Install the client

Recommended default with `venv`:

```bash
git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client

python3 -m venv .venv
source .venv/bin/activate

pip install -e ".[all]"
```

Alternative without `venv`:

```bash
git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client

python3 -m pip install --break-system-packages -e ".[all]"
```

Notes:

- `venv` is still the safer default for most users.
- Running without `venv` can be practical on Raspberry Pi when you intentionally rely on system-installed packages such as `python3-rpi.gpio`.
- If you use the no-`venv` path, later commands that mention `.venv/bin/python` should be replaced with `python3`.

### 3. Optional extras

All core dependencies (opencv, pyserial, paho-mqtt, psutil, etc.) are installed from `pyproject.toml` via `.[all]`. The only extras you need to install manually are cloud TTS engines:

| Extra | Command | Needed for |
|-------|---------|-----------|
| boto3 | `pip install boto3` | Amazon Polly TTS |
| google-cloud-texttospeech | `pip install google-cloud-texttospeech` | Google Cloud TTS |

### 4. Create your config

```bash
cp config.example.yaml config.yaml
```

At minimum, edit `config.yaml` and set:

```yaml
server:
  api_url: https://botparty.live
  livekit_url: wss://botparty.live
  claim_token: PASTE_YOUR_CLAIM_TOKEN_HERE
```

Then choose your real `hardware.type` and `video.type`.

For the normal default path, start with:

```yaml
video:
  type: ffmpeg
  options: {}
```

For the lowest-latency Raspberry Pi path with hardware H.264, keep `type: ffmpeg`. The client prefers hardware H.264 automatically and falls back to software H.264 if needed:

```yaml
video:
  type: ffmpeg
  options:
    target_bitrate_kbps: 1200
```

Only set `video_codec` if you want to force a specific FFmpeg H.264 encoder.

The client automatically manages `botparty-streamer` in `.botparty/bin` and auto-downloads or updates it when needed. You can also install it manually:

```bash
./scripts/install-botparty-streamer.sh
```

This is the recommended high-performance path:

- ffmpeg captures from the camera and encodes H.264
- botparty-streamer publishes directly to LiveKit
- botparty-streamer is our selfmade video transmitter for maximum performance, low CPU usage and low latency

### 5. Run as a service (optional)

If you installed without `venv`, use `ExecStart=/usr/bin/python3 -m botparty_robot` instead.

Create `/etc/systemd/system/botparty-robot.service`:

```ini
[Unit]
Description=BotParty Robot Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/home/<your-user>/botparty-client
ExecStart=/home/<your-user>/botparty-client/.venv/bin/python -m botparty_robot
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Then enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now botparty-robot
sudo journalctl -u botparty-robot -f   # follow logs
```

---

## Jetson Nano / Orin

JetPack includes Python 3.8 by default; you need 3.10+.

```bash
sudo apt install -y python3.10 python3.10-venv
python3.10 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

For camera capture use the `ffmpeg` video profile with `input_driver: v4l2`. The `ffmpeg_libcamera` profile is Raspberry Pi-specific.

---

## Ubuntu / Debian (x86)

Same steps as Raspberry Pi but skip GPIO packages:

```bash
sudo apt install -y python3-pip python3-venv ffmpeg alsa-utils espeak mpg123
git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

---

## Verifying the install

```bash
cp config.example.yaml config.yaml
```

Then edit `config.yaml` before the first run.

```bash
source .venv/bin/activate
python -m botparty_robot   # starts with config.yaml in cwd
```

Without `venv`:

```bash
python3 -m botparty_robot   # starts with config.yaml in cwd
```

If you used the GPIO step above, log out and back in once before your first real hardware test so the `gpio` group membership is active.

## botparty-streamer download

The installer script chooses the correct BotParty-tested `botparty-streamer` binary for the current machine architecture.

Today the main supported helper builds are:

- `linux-arm64` for Raspberry Pi 4/5 with 64-bit Raspberry Pi OS
- `linux-amd64` for x86_64 Ubuntu/Debian systems

Example asset URL:

```text
http://dl.botparty.live/botparty-streamer-v0.1.3-linux-arm64
```

Install default version:

```bash
./scripts/install-botparty-streamer.sh
```

Default output path is `<repo>/.botparty/bin/botparty-streamer`.

Install a specific version:

```bash
./scripts/install-botparty-streamer.sh v0.1.3
```

Install with explicit architecture selection:

```bash
# x86_64 / amd64
./scripts/install-botparty-streamer.sh --arch amd64

# Raspberry Pi (arm64)
./scripts/install-botparty-streamer.sh --arch rpi
```

Custom output directory:

```bash
./scripts/install-botparty-streamer.sh --arch amd64 --dir /opt/botparty/bin
```
