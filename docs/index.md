# BotParty Robot Client

The BotParty Robot Client is the software that runs on your robot and connects it to the BotParty platform. It handles the WebSocket control channel, publishes live video via LiveKit, and drives your motors, servos, or any other hardware you have attached.

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
# Python 3.10+ required
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

See [Installation](installation.md) for platform-specific steps (Raspberry Pi, Jetson Nano, Ubuntu x86).

### 2. Configure

```bash
cp config.example.yaml config.yaml
```

Open `config.yaml` and fill in at minimum:

```yaml
server:
  api_url: https://botparty.io        # or your self-hosted URL
  livekit_url: wss://livekit.botparty.io
  claim_token: PASTE_YOUR_CLAIM_TOKEN_HERE
```

Get your `claim_token` from the BotParty dashboard under **My Robots → Claim Token**.

See [Configuration](configuration.md) for the full reference.

### 3. Run

```bash
python -m botparty_robot
```

You should see output like:

```
2025-01-01 12:00:00 [INFO] botparty: BotParty Robot Client v0.1.0
2025-01-01 12:00:00 [INFO] botparty: API: https://botparty.io
2025-01-01 12:00:00 [INFO] botparty: Hardware: l298n
2025-01-01 12:00:00 [INFO] botparty: Video: ffmpeg
2025-01-01 12:00:00 [INFO] botparty: TTS: espeak (enabled=True)
2025-01-01 12:00:00 [INFO] botparty.gateway: connected
```

---

## Documentation sections

| Section | Description |
|---------|-------------|
| [Installation](installation.md) | OS-specific install guide |
| [Configuration](configuration.md) | Full `config.yaml` reference |
| [Hardware adapters](hardware/index.md) | Motor drivers, servo boards, serial, MQTT, custom |
| [Video profiles](video/index.md) | Camera capture and streaming to LiveKit |
| [TTS profiles](tts/index.md) | Text-to-speech engines |
| [Troubleshooting](troubleshooting.md) | Common issues and fixes |
