# BotParty Robot Client

The official Python client for connecting your robot to **BotParty**.

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
  device: "/dev/video0"  # or "picamera2" for Pi Camera
  backend: "v4l2"
  fourcc: "MJPG"
  buffer_size: 1
  warmup_frames: 4

controls:
  gpio_enabled: true
  motor_left_forward: 17
  motor_left_backward: 18
  motor_right_forward: 22
  motor_right_backward: 23
  servo_camera_pan: 12
  servo_camera_tilt: 13

safety:
  emergency_stop_pin: 27
  max_run_time_ms: 2000  # Auto-stop if no command received
  latency_threshold_ms: 300
```

## Custom Robot Handlers

Create your own control handler:

```python
from botparty_robot.handlers import BaseHandler

class MyRobotHandler(BaseHandler):
    def on_command(self, command: str, value=None):
        if command == "forward":
            self.motor_forward()
        elif command == "stop":
            self.motor_stop()

    def on_emergency_stop(self):
        self.motor_stop()
        self.all_gpio_low()
```

## ROS2 Integration

For ROS2 robots, use the ROS2 handler:

```python
from botparty_robot.handlers.ros2 import ROS2Handler
handler = ROS2Handler(node_name="botparty_teleop")
```

## Streaming Tuning

The robot client now exposes a few camera-capture knobs that matter for smoother low-latency streams:

- `backend`: camera API to ask OpenCV for. On Raspberry Pi/Linux, `v4l2` is the best default.
- `fourcc`: preferred capture format. `MJPG` often helps USB cameras hold higher resolutions/FPS more reliably.
- `buffer_size`: keep this low, usually `1`, to avoid stale buffered frames.
- `warmup_frames`: discard the first few frames while the camera settles.

Recommended starting points:

- Pi + USB camera: `1280x720`, `fps: 24` or `30`, `backend: "v4l2"`, `fourcc: "MJPG"`, `buffer_size: 1`
- Weak CPU or unstable stream: drop to `960x540@24` or `640x360@30`
- If a camera misbehaves with `MJPG`, try `fourcc: null`
