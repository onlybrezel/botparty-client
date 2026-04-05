# OpenCV Video Profile

The `opencv` profile captures frames using OpenCV's `VideoCapture`. It requires no system-level dependencies beyond the `opencv-python` package and is the easiest profile to get running on any platform.

```yaml
video:
  type: "opencv"
  options: {}
```

---

## When to use OpenCV

- Quick testing on a development machine
- Platforms where FFmpeg is unavailable or hard to install
- Situations where the camera does not work with V4L2 directly

The tradeoff is slightly higher CPU usage and more latency compared to `ffmpeg`. For production robots on Raspberry Pi, `ffmpeg` is recommended.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `target_bitrate_kbps` | int | `null` | Cap LiveKit video bitrate |

Camera settings (resolution, FPS, backend) come from the `camera` block:

```yaml
camera:
  device: "/dev/video0"   # or an integer index: 0
  width: 1280
  height: 720
  fps: 30
  backend: "v4l2"         # "v4l2", "gstreamer", "ffmpeg", or "auto"
```

### `camera.backend`

Selects the OpenCV capture backend:

| Value | OpenCV flag | Notes |
|-------|-------------|-------|
| `auto` / `any` | (default) | OpenCV picks automatically |
| `v4l2` | `CAP_V4L2` | Linux V4L2 — most reliable on Pi |
| `gstreamer` | `CAP_GSTREAMER` | GStreamer pipeline |
| `ffmpeg` | `CAP_FFMPEG` | FFmpeg backend inside OpenCV |

---

## Dependencies

```bash
pip install opencv-python-headless
```

Use `opencv-python-headless` (no GUI dependencies) unless you also need `cv2.imshow()`.

---

## Troubleshooting

**"Can't open camera"**

Try an integer device index instead of a path:

```yaml
camera:
  device: 0    # OpenCV device index
```

**Frames are very slow / 1 FPS**

OpenCV may be defaulting to a slow capture mode. Force V4L2:

```yaml
camera:
  backend: "v4l2"
  fourcc: "MJPG"
```
