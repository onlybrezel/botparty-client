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
| `livekit_url` | string | — | LiveKit WebSocket URL (e.g. `wss://botparty.live/rtc`) |
| `claim_token` | string | — | **Required.** Your robot's claim token from the dashboard |

```yaml
server:
  api_url: "https://botparty.live"
  livekit_url: "wss://botparty.live/rtc"
  claim_token: "your-claim-token-here"
```

> **Claim token**: generate or copy this from the BotParty dashboard under **My Robots → Claim Token**. Treat it like a password; do not commit it to version control.

---

## `camera`

Describes the physical camera device. These settings are passed to the active video profile.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `device` | string/int | `/dev/video0` | V4L2 device path such as `/dev/video0`, or a numeric OpenCV device index such as `0` |
| `width` | int | `1280` | Capture width in pixels |
| `height` | int | `720` | Capture height in pixels |
| `fps` | int | `30` | Target frames per second |
| `fourcc` | string | `MJPG` | Pixel format passed to ffmpeg (`MJPG` or `YUYV`) |
| `backend` | string | `v4l2` | V4L2/OpenCV backend hint (`v4l2`, `ffmpeg`, `auto`) |
| `buffer_size` | int | `1` | V4L2 capture buffer size (1–8). Keep at 1 for minimum latency. |
| `warmup_frames` | int | `4` | Frames to discard after opening the camera to flush stale frames. |

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 30
```

Run `ls /dev/video*` to find your camera's device path.

> For most robots, one camera is enough. If you want a front + rear setup or another extra view, use the optional [`cameras`](#cameras-optional) block below and read [Multi-camera](multi-camera.md).

---

## `cameras` (optional)

Optional block for robots with more than one camera.

How it behaves:

- if `cameras` is omitted, the client uses the legacy `camera` + `video` blocks
- if `cameras` contains one entry, the robot should still behave like a single-camera robot
- if `cameras` contains multiple entries, each camera is published as its own track in the same room

Suggested shape:

| Key | Type | Description |
|-----|------|-------------|
| `id` | string | Stable machine-readable ID such as `front` or `rear` |
| `label` | string | Human-friendly display name |
| `role` | string | Suggested values: `primary`, `secondary`, `rear`, `arm`, `dock` |
| `enabled` | bool | Enables or disables this camera without deleting the config |
| `device` | string/int | Video device path or index |
| `width` / `height` / `fps` | int | Per-camera capture settings |
| `fourcc` / `backend` / `buffer_size` / `warmup_frames` | mixed | Same meaning as the top-level `camera` block |
| `publish_mode` | string | Suggested values: `always_on`, `preview_only`, `on_demand` |
| `video` | object | Per-camera video profile and options |

Example:

```yaml
cameras:
  - id: "front"
    label: "Front"
    role: "primary"
    enabled: true
    device: "/dev/video0"
    width: 1280
    height: 720
    fps: 24
    fourcc: "MJPG"
    publish_mode: "always_on"
    video:
      type: "ffmpeg"
      options:
        target_bitrate_kbps: 1200

  - id: "rear"
    label: "Rear"
    role: "secondary"
    enabled: true
    device: "/dev/video2"
    width: 640
    height: 360
    fps: 12
    fourcc: "MJPG"
    publish_mode: "preview_only"
    video:
      type: "ffmpeg"
      options:
        target_bitrate_kbps: 350
```

Typical uses:

- front + rear driving cameras
- arm/gripper close-up camera
- docking or parking assist camera

See [Multi-camera](multi-camera.md) for recommended settings and practical notes.

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
| `ffmpeg` | FFmpeg via V4L2 with automatic direct-publisher mode when available **(recommended)** |
| `ffmpeg_arecord` | FFmpeg + microphone audio via ALSA `arecord` |
| `ffmpeg_libcamera` | `libcamera-vid` piped into FFmpeg — for Raspberry Pi Camera Module |
| `opencv` | Pure-Python OpenCV fallback — no FFmpeg needed |
| `none` | Disable video publishing entirely |
| `ffmpeg_hud` | FFmpeg capture with HUD overlay support |
| `cozmo_vid` | Video from an attached Cozmo robot |
| `vector_vid` | Video from an attached Vector robot |

See [Video profiles](video/index.md) for full details on each profile and its options.

In a multi-camera setup, each camera may have its own `video` block with its own `type` and `options`.

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

`ffmpeg` startup behavior:

- uses the managed helper at `.botparty/bin/botparty-streamer`
- if missing or outdated, resolves active version from `https://stats.botparty.live/get_active_version.php?app=streamer` and auto-downloads it
- if direct mode is unavailable, falls back to SDK publish automatically

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
| `mc33926` | Pololu dual MC33926 motor driver |
| `mdd10` | Cytron MDD10 10A PWM motor driver |
| `motozero` | MotoZero 4-motor GPIO board |
| `thunderborg` | PiBorg ThunderBorg I2C driver |
| `gopigo2` | Dexter Industries GoPiGo 2 |
| `gopigo3` | Dexter Industries GoPiGo 3 |
| `megapi_board` | Makeblock MegaPi tracked robot |
| `telly` | Telly serial controller preset |
| `max7219` | MAX7219 LED matrix / face display |
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
| `filter_urls` | bool | `false` | Skip messages that contain URLs |
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
| `custom` | Load your own Python TTS class | Importable class path in `tts.options.class` |
| `espeak_loop` | Legacy alias for `espeak` | Same as `espeak` |
| `cozmo_tts` | Speak through an attached Cozmo robot | `cozmo[camera]` |
| `vector_tts` | Speak through an attached Vector robot | `anki_vector` |

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
| `emergency_stop_pin` | int | — | Optional BCM GPIO pin number. A falling edge on this pin triggers an immediate emergency stop. |
| `max_run_time_ms` | int | `2000` | Auto-stop motors after this many milliseconds with no new command |

```yaml
safety:
  max_run_time_ms: 2000
```
