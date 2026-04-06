# Configuration Reference

The client is configured via a single `config.yaml` file in the current working directory.

Start from the example:

```bash
cp config.example.yaml config.yaml
```

---

## `server`

Connection settings for the BotParty server.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `api_url` | string | — | Base URL of the BotParty instance (e.g. `https://botparty.live`) |
| `livekit_url` | string | — | LiveKit WebSocket URL (e.g. `wss://botparty.live/livekit`) |
| `claim_token` | string | — | **Required.** Your robot's claim token from the dashboard |

```yaml
server:
  api_url: "https://botparty.live"
  livekit_url: "wss://botparty.live/livekit"
  claim_token: "your-claim-token-here"
```

> **Claim token**: generate or copy this from the BotParty dashboard under **My Robots → Claim Token**. Treat it like a password; do not commit it to version control.

---

## `camera`

Describes the physical camera device. These settings are passed to the active video profile.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `device` | string | `/dev/video0` | V4L2 device path or integer index for OpenCV |
| `width` | int | `1280` | Capture width in pixels |
| `height` | int | `720` | Capture height in pixels |
| `fps` | int | `30` | Target frames per second |
| `fourcc` | string | `MJPG` | Pixel format passed to ffmpeg (`MJPG` or `YUYV`) |
| `backend` | string | `auto` | OpenCV backend hint (`v4l2`, `gstreamer`, `ffmpeg`, `auto`) |

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 30
```

Run `ls /dev/video*` to find your camera's device path.

---

## `video`

Selects the video capture and streaming pipeline.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `type` | string | `ffmpeg` | Video profile name (see below) |
| `options` | object | `{}` | Profile-specific options |

### Available video profiles

| Type | Description |
|------|-------------|
| `ffmpeg` | FFmpeg via V4L2 — best quality, lowest latency **(recommended)** |
| `ffmpeg_arecord` | FFmpeg + microphone audio via ALSA `arecord` |
| `ffmpeg_libcamera` | `libcamera-vid` piped into FFmpeg — for Raspberry Pi Camera Module |
| `opencv` | Pure-Python OpenCV fallback — no FFmpeg needed |

See [Video profiles](video/index.md) for full details on each profile and its options.

```yaml
# Minimal — ffmpeg on /dev/video0
video:
  type: "ffmpeg"
  options: {}

# With USB microphone
video:
  type: "ffmpeg_arecord"
  options:
    audio_device: "default"
    audio_sample_rate: 48000
    audio_channels: 1
```

---

## `hardware`

Selects the motor/servo driver.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `type` | string | `none` | Hardware adapter name (see below) |
| `options` | object | `{}` | Adapter-specific options |

### Available hardware adapters

| Type | Description |
|------|-------------|
| `none` | No movement — safe default for video-only testing |
| `l298n` | L298N-style GPIO H-bridge (most common cheap driver) |
| `adafruit_pwm` | Adafruit PCA9685 PWM/Servo HAT |
| `motor_hat` | Adafruit DC + Stepper Motor HAT |
| `serial_board` | Arduino / any microcontroller over USB serial |
| `mqtt_pub` | Publish commands to an MQTT broker (e.g. ROS2 bridge) |
| `pololu` | Pololu DRV8835 dual motor driver |
| `mdd10` | Cytron MDD10 10A PWM motor driver |
| `motozero` | MotoZero 4-motor GPIO board |
| `thunderborg` | PiBorg ThunderBorg I2C driver |
| `gopigo2` | Dexter Industries GoPiGo 2 |
| `gopigo3` | Dexter Industries GoPiGo 3 |
| `maestro_servo` | Pololu Maestro dual-servo drive |
| `navq` | NXP NavQ / MAVSDK offboard control |
| `cozmo` | Anki Cozmo |
| `vector` | Anki Vector |
| `owi_arm` | OWI 535 USB Robotic Arm |
| `custom` | Your own `hardware_custom.py` adapter |

See [Hardware adapters](hardware/index.md) for setup and wiring guides.

```yaml
# L298N example
hardware:
  type: "l298n"
  options:
    forward_pins: [17]
    backward_pins: [18]
    left_pins: [22]
    right_pins: [23]
    drive_seconds: 0.35
    turn_seconds: 0.20
```

---

## `tts`

Text-to-speech configuration. When enabled, chat messages from viewers are spoken through the robot's speaker.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Enable TTS |
| `type` | string | `none` | TTS engine (see below) |
| `playback_device` | string | `default` | ALSA device for audio output |
| `volume` | int | `70` | Speaker volume 0–100 |
| `chat_to_tts` | bool | `true` | Speak all chat messages |
| `filter_urls` | bool | `true` | Strip URLs before speaking |
| `allow_anonymous` | bool | `true` | Speak messages from guests |
| `blocked_senders` | list | `[]` | Usernames to silence |
| `delay_ms` | int | `0` | Delay before speaking (ms) |
| `options` | object | `{}` | Engine-specific options |

### Available TTS engines

| Type | Description | Requires |
|------|-------------|---------|
| `none` | Disabled | — |
| `espeak` | eSpeak (offline, robotic voice) | `sudo apt install espeak` |
| `pico` | SVOX Pico (offline, natural voice) | `sudo apt install libttspico-utils` |
| `festival` | Festival Speech Synthesis (offline) | `sudo apt install festival` |
| `polly` | Amazon Polly (cloud) | `pip install boto3` + AWS credentials |
| `google_cloud` | Google Cloud TTS (cloud) | `pip install google-cloud-texttospeech` + service account JSON |

Run `aplay -l` to list ALSA playback devices. `plughw:1,0` addresses card 1, device 0.

See [TTS profiles](tts/index.md) for full engine documentation.

```yaml
tts:
  enabled: true
  type: "espeak"
  playback_device: "default"
  volume: 75
  filter_urls: true
  options:
    voice: "en-us"
    speed: 165
```

---

## `safety`

Limits that protect the robot from runaway commands.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_run_time_ms` | int | `2000` | Auto-stop motors after this many milliseconds with no new command |
| `latency_threshold_ms` | int | `300` | Drop commands that arrive with more than this much latency |

```yaml
safety:
  max_run_time_ms: 2000
  latency_threshold_ms: 300
```

`latency_threshold_ms` is especially important for robots operating in constrained environments. If a command was queued during a connection hiccup it may arrive 500 ms late — by then the physical situation may have changed. Setting a threshold of 300 ms means the robot ignores commands older than 300 ms and simply stops instead of acting on stale input.
