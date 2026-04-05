# BotParty Robot Client

The official Python client for connecting your robot to **BotParty**.

📖 **Full documentation is in the [`docs/`](docs/index.md) folder.**

| Guide | |
|-------|-|
| [Getting started](docs/index.md) | Overview and quick start |
| [Installation](docs/installation.md) | Raspberry Pi, Jetson, Ubuntu, Docker |
| [Configuration](docs/configuration.md) | Full `config.yaml` reference |
| [Hardware adapters](docs/hardware/index.md) | L298N, serial, MQTT, PWM HAT, custom, and more |
| [Video profiles](docs/video/index.md) | FFmpeg, libcamera, OpenCV |
| [TTS profiles](docs/tts/index.md) | eSpeak, Pico, Festival, Polly, Google Cloud |
| [Troubleshooting](docs/troubleshooting.md) | Common issues and fixes |

## Supported Hardware
- Raspberry Pi 3B+ / 4 / 5 (with Pi Camera or USB camera)
- NVIDIA Jetson Nano / Orin
- Any Linux system with a camera and Python 3.11+

## Quick Start

```bash
# 1. Clone this folder to your robot
# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy config
cp config.example.yaml config.yaml
# Edit config.yaml with your claim token from the BotParty dashboard

# 5. Run
python -m botparty_robot
```

## Configuration

Edit `config.yaml`:

```yaml
server:
  api_url: "https://your-botparty-instance.com"
  livekit_url: "wss://your-botparty-instance.com:7880"
  claim_token: "YOUR_CLAIM_TOKEN_FROM_DASHBOARD"

camera:
  width: 1280
  height: 720
  fps: 30
  device: "/dev/video0"
  backend: "v4l2"
  fourcc: "MJPG"
  buffer_size: 1
  warmup_frames: 4

# Video pipeline – selects how the camera feed is captured and streamed.
# Available: ffmpeg, ffmpeg_arecord, ffmpeg_libcamera, opencv, none
video:
  type: "ffmpeg"
  options: {}

# Motor controller – pick the type that matches your board.
hardware:
  type: "l298n"
  options:
    forward_pins: [17]
    backward_pins: [18]
    left_pins: [22]
    right_pins: [23]
    drive_seconds: 0.35
    turn_seconds: 0.20

safety:
  max_run_time_ms: 2000
  latency_threshold_ms: 300

tts:
  enabled: true
  type: "espeak"
  playback_device: "Headphones"
  volume: 75
  options:
    voice: "de"
    voice_variant: "m3"
    speed: 165
```

## Hardware Profiles

The client now follows a RemoTV-style `hardware/` structure inside `botparty_robot/hardware`.

Implemented profiles:

- `none`
- `l298n`
- `adafruit_pwm`
- `gopigo2`
- `gopigo3`
- `maestro_servo`
- `max7219`
- `mc33926`
- `mdd10`
- `megapi_board`
- `motor_hat`
- `motozero`
- `mqtt_pub`
- `navq`
- `owi_arm`
- `pololu`
- `serial_board`
- `telly`
- `thunderborg`
- `cozmo`
- `vector`
- `custom`

The hardware names from `remotv/controller/hardware` are now represented directly in BotParty's config shape, plus the `custom` path for project-specific robots.

## Video Profiles

The client now also has a dedicated `botparty_robot/video` package modeled after `remotv/controller/video`.

Available profiles:

- `opencv`
- `ffmpeg`
- `ffmpeg_arecord`
- `ffmpeg_hud`
- `ffmpeg_libcamera`
- `cozmo_vid`
- `vector_vid`
- `none`

`ffmpeg_arecord` now resolves friendly ALSA capture names too, so values like `audio_device: "Webcam"` or `audio_device: "USB"` work in addition to raw `hw:` devices.

## TTS Profiles

The client now also mirrors `remotv/controller/tts` through `botparty_robot/tts`.

Available profiles:

- `none`
- `espeak`
- `espeak_loop`
- `festival`
- `google_cloud`
- `pico`
- `polly`
- `cozmo_tts`
- `vector_tts`
- `custom`

Example config:

```yaml
tts:
  enabled: true
  type: "espeak"
  playback_device: "Headphones"
  volume: 75
  filter_urls: false
  allow_anonymous: true
  blocked_senders: []
  delay_ms: 0
  options:
    voice: "de"
    voice_variant: "m3"
    speed: 165
```

Supported TTS control commands on the data channel:

- `say` with a string payload
- `say:Hallo zusammen`
- `speak`
- `tts:say`
- `tts:mute`
- `tts:unmute`
- `tts:volume`

For structured payloads, `say` and `tts:say` also accept objects like:

```json
{
  "command": "tts:say",
  "value": {
    "message": "Hallo aus BotParty",
    "sender": "julien",
    "anonymous": false
  }
}
```

`playback_device` accepts both direct ALSA targets like `plughw:0,0` and friendly names like `Headphones`, while `video.options.audio_device` does the same for microphone capture.

Example config:

```yaml
hardware:
  type: "l298n"
  options:
    forward_pins: [17]
    backward_pins: [18]
    left_pins: [22]
    right_pins: [23]
    drive_seconds: 0.35
    turn_seconds: 0.20

video:
  type: "ffmpeg"
  options:
    ffmpeg_path: "ffmpeg"
```

Another example with serial hardware and microphone capture:

```yaml
hardware:
  type: "serial_board"
  options:
    device: "/dev/ttyUSB0"
    baud_rate: 115200

video:
  type: "ffmpeg_arecord"
  options:
    ffmpeg_path: "ffmpeg"
    arecord_path: "arecord"
    audio_device: "default"
    audio_sample_rate: 48000
    audio_channels: 1
```

## Custom Hardware

Create your own adapter class and point `hardware.options.class` at it:

```python
from typing import Any
from botparty_robot.hardware.base import BaseHardware

class MyRobotHandler(BaseHardware):
    def on_command(self, command: str, value: Any = None) -> None:
        if command == "forward":
            self.motor_forward()  # your code here
        elif command == "stop":
            self.motor_stop()

    def emergency_stop(self) -> None:
        self.motor_stop()
```

Then in `config.yaml`:

```yaml
hardware:
  type: "custom"
  options:
    class: "my_robot.handler.MyRobotHandler"
```

## Optional Profile Dependencies

The base `requirements.txt` covers the default BotParty path plus common bridges. Some hardware and robot-specific profiles still need platform-specific libraries on the target machine:

- `adafruit_pwm` / `motor_hat`: Adafruit PCA9685 / MotorHAT libraries
- `gopigo2` / `gopigo3`: Dexter Industries packages
- `max7219`: `spidev`
- `mc33926`: `dual-mc33926-motor-driver-rpi`
- `mdd10`, `motozero`, `l298n`: `RPi.GPIO`
- `megapi_board`: `megapi`
- `navq`: `mavsdk`
- `owi_arm`: `pyusb`
- `pololu`: `pololu-drv8835-rpi`
- `thunderborg`: ThunderBorg Python library
- `cozmo`, `cozmo_vid`: Cozmo SDK
- `vector`, `vector_vid`: `anki_vector`
- `espeak`, `espeak_loop`: `espeak`, `aplay`
- `festival`: `festival`, `text2wave`, `aplay`
- `pico`: `libttspico-utils` / `pico2wave`, `aplay`
- `google_cloud`: `google-cloud-texttospeech`, `google-auth`, `aplay`
- `polly`: `boto3`, `mpg123`
- `cozmo_tts`: Cozmo SDK
- `vector_tts`: `anki_vector`

## Streaming Tuning

The robot client exposes several camera-capture knobs for smoother low-latency streams:

- `video.type`: selects the capture pipeline (`ffmpeg`, `ffmpeg_arecord`, `ffmpeg_libcamera`, `ffmpeg_hud`, `opencv`, `none`)
- `camera.backend`: camera API asked of OpenCV. On Raspberry Pi/Linux, `v4l2` is the best default.
- `camera.fourcc`: preferred capture format. `MJPG` often helps USB cameras hold higher resolutions/FPS more reliably.
- `camera.buffer_size`: keep this low, usually `1`, to avoid stale buffered frames.
- `camera.warmup_frames`: discard the first few frames while the camera settles.

Recommended starting points:

- Pi + USB camera: `video.type: "ffmpeg"` if ffmpeg is installed; otherwise `video.type: "opencv"`, `backend: "v4l2"`, `fourcc: "MJPG"`, `buffer_size: 1`
- For OpenCV capture on weaker Pis, expect `1280x720@30` to be too ambitious; `640x360@30` or `1280x720@15` is often more realistic
- Weak CPU or unstable stream: drop to `960x540@24` or `640x360@30`
- If a camera misbehaves with `MJPG`, try `fourcc: null`
