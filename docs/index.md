# BotParty Robot Client

The BotParty Robot Client is the software that runs on your robot and connects it to the BotParty platform. It handles the WebSocket control channel, publishes live video via LiveKit, and drives your motors, servos, or any other hardware you have attached.

Single-camera robots remain the default, but the client is designed to grow into multi-camera setups such as front + rear streaming without forcing a separate deployment model.

---

## How it works

1. The client connects to the BotParty API using a **claim token** you generate from the dashboard.
2. Once authenticated the robot is marked online and joins a LiveKit room where its camera is published.
3. When a viewer controls the robot, the server sends `control:command` events over the WebSocket. The client dispatches those to the configured **hardware adapter**.
4. Text-to-speech messages from viewers are optionally spoken through a **TTS profile**.

```
Dashboard ──► BotParty API ──► WebSocket gateway ──► botparty-client
                                                          ├── Hardware adapter (motors / servos)
                                                          ├── Video profile (camera → LiveKit)
                                                          └── TTS profile (speaker)
```

---

## Quick start

### 1. Install

```bash
sudo apt update
sudo apt install -y git python3-pip python3-venv ffmpeg

git clone https://github.com/onlybrezel/botparty-client.git
cd botparty-client

# Python 3.10+ required
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
```

If you prefer to run without `venv`, see [Installation](installation.md) for the system-Python variant as well.

See [Installation](installation.md) for the full Raspberry Pi / Jetson / Ubuntu guide, GPIO packages, audio/TTS packages, and service setup.

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and fill in at minimum:

```yaml
server:
  api_url: https://botparty.live
  livekit_url: wss://botparty.live/rtc
  claim_token: PASTE_YOUR_CLAIM_TOKEN_HERE
```

Get your `claim_token` from the BotParty dashboard under **My Robots → Claim Token**.

See [Configuration](configuration.md) for the full reference.

### 3. Run

```bash
python -m botparty_robot
```

Without `venv`, run:

```bash
python3 -m botparty_robot
```

You should see output like:

```
2025-01-01 12:00:00 [INFO] botparty: BotParty Robot Client v0.1.3
2025-01-01 12:00:00 [INFO] botparty: API: https://botparty.live
2025-01-01 12:00:00 [INFO] botparty: Hardware: l298n
2025-01-01 12:00:00 [INFO] botparty: Video: ffmpeg
2025-01-01 12:00:00 [INFO] botparty: TTS: espeak (enabled=True)
2025-01-01 12:00:01 [INFO] botparty.gateway: Control websocket connected
```

---

## Documentation sections

| Section | Description |
|---------|-------------|
| [Installation](installation.md) | OS-specific install guide |
| [Configuration](configuration.md) | Full `config.yaml` reference |
| [Client mixins](client-mixins.md) | MRO, shared state, and concurrency model |
| [Multi-camera](multi-camera.md) | Front/rear camera setups, good defaults, and practical tips |
| [Hardware adapters](hardware/index.md) | Motor drivers, servo boards, serial, MQTT, custom |
| [Video profiles](video/index.md) | Camera capture and streaming to LiveKit |
| [TTS profiles](tts/index.md) | Text-to-speech engines |
| [Troubleshooting](troubleshooting.md) | Common issues and fixes |
