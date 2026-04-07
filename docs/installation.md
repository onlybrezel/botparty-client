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
sudo apt install -y python3-pip python3-venv ffmpeg \
    libatlas-base-dev libopenblas-dev   # needed for opencv
```

For GPIO access (L298N, MDD10, MotoZero adapters):

```bash
sudo apt install -y python3-rpi.gpio
sudo usermod -aG gpio $USER   # log out and back in after this
```

For audio / TTS:

```bash
sudo apt install -y alsa-utils espeak mpg123
```

### 2. Install the client

```bash
git clone https://github.com/your-org/botparty-client.git
cd botparty-client

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Optional extras

All core dependencies (opencv, pyserial, paho-mqtt, etc.) are already in `requirements.txt` and installed in the step above. The only extras you need to install manually are cloud TTS engines:

| Extra | Command | Needed for |
|-------|---------|-----------|
| boto3 | `pip install boto3` | Amazon Polly TTS |
| google-cloud-texttospeech | `pip install google-cloud-texttospeech` | Google Cloud TTS |

### 4. Run as a service (optional)

Create `/etc/systemd/system/botparty-robot.service`:

```ini
[Unit]
Description=BotParty Robot Client
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/botparty-client
ExecStart=/home/pi/botparty-client/.venv/bin/python -m botparty_robot
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
pip install -r requirements.txt
```

For camera capture use the `ffmpeg` video profile with `input_driver: v4l2`. The `ffmpeg_libcamera` profile is Raspberry Pi-specific.

---

## Ubuntu / Debian (x86)

Same steps as Raspberry Pi but skip GPIO packages:

```bash
sudo apt install -y python3-pip python3-venv ffmpeg alsa-utils espeak mpg123
git clone https://github.com/your-org/botparty-client.git
cd botparty-client
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Verifying the install

```bash
source .venv/bin/activate
python -m botparty_robot   # starts with config.yaml in cwd
```
