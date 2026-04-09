# GStreamer Video Profile

The `gstreamer` and `gstreamer_arecord` profiles are the optional low-latency path for Raspberry Pi robots.

They do **not** replace the normal default setup. For most robots, start with `ffmpeg`.

Use `gstreamer` only when you specifically want:

- lower video latency on Raspberry Pi
- Raspberry Pi H.264 hardware encoding via `h264_v4l2m2m`
- direct publishing with `gstreamer-publisher`

---

## When to use it

Good fit:

- Raspberry Pi 4 / 5
- USB cameras that work well with `MJPG`
- robots where low latency matters more than absolute simplicity

Usually not needed:

- first-time setup
- development on a laptop/desktop
- exotic camera sources where the normal `ffmpeg` path already works well

If you just want the easiest setup, stay on `video.type: "ffmpeg"`.

---

## What it does

The BotParty-tested path is:

1. `ffmpeg` reads the camera
2. `h264_v4l2m2m` does Raspberry Pi H.264 hardware encoding
3. `gstreamer-publisher` publishes the encoded stream directly to LiveKit

That means the heavy Python frame bridge is skipped for this profile.

Important:

- the **H.264 encode** is hardware-accelerated
- the **entire pipeline** is not 100% hardware
- MJPEG decode, conversion, and upload glue can still use CPU

---

## Install

Install the extra packages:

```bash
sudo apt update
sudo apt install -y ffmpeg \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad
```

Then install the BotParty-tested publisher binary:

```bash
./scripts/install-gstreamer-publisher.sh
```

If you also want microphone audio with `gstreamer_arecord`:

```bash
sudo apt install -y gstreamer1.0-alsa
```

---

## Basic config

### Video only

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 20
  fourcc: "MJPG"

video:
  type: "gstreamer"
  options:
    publisher_path: "/home/pi/bin/gstreamer-publisher"
    publish_backend: "ffmpeg"
    video_codec: "h264_v4l2m2m"
    target_bitrate_kbps: 1200
```

### Video + microphone

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 20
  fourcc: "MJPG"

video:
  type: "gstreamer_arecord"
  options:
    publisher_path: "/home/pi/bin/gstreamer-publisher"
    publish_backend: "ffmpeg"
    video_codec: "h264_v4l2m2m"
    target_bitrate_kbps: 1200
    audio_device: "default"
    audio_sample_rate: 48000
    audio_channels: 1
    audio_bitrate_kbps: 64
```

---

## Important options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `publisher_path` | string | `gstreamer-publisher` | Path to the helper binary |
| `publish_backend` | string | `auto` | `ffmpeg` for the BotParty-tested Pi path, `gstreamer` for pure GStreamer fallback |
| `video_codec` | string | auto | H.264 encoder, usually `h264_v4l2m2m` on Raspberry Pi |
| `target_bitrate_kbps` | int | profile default | Upload bitrate cap |
| `publish_fps` | float | auto | Explicit publish FPS override |
| `audio_device` | string | `default` | ALSA capture device for `gstreamer_arecord` |
| `audio_sample_rate` | int | `48000` | Audio sample rate |
| `audio_channels` | int | `1` | Audio channels |
| `audio_bitrate_kbps` | int | `64` | Opus bitrate for `gstreamer_arecord` |

---

## FPS and auto-caps

If you set `camera.fps: 30` but only see `15 fps` published, that can be expected.

Why:

- the client may apply a conservative publish-FPS cap on weaker Raspberry Pi hardware
- `camera.fps` is the requested capture rate
- `publish_fps` is the actual target rate for publishing

If you want to force a specific publish rate, set it explicitly:

```yaml
video:
  type: "gstreamer"
  options:
    publish_backend: "ffmpeg"
    video_codec: "h264_v4l2m2m"
    publish_fps: 30
```

The startup log will show the effective publish rate.

---

## Multi-camera

`gstreamer` and `gstreamer_arecord` can also be used per camera:

```yaml
cameras:
  - id: "front"
    device: "/dev/v4l/by-path/...front..."
    width: 1280
    height: 720
    fps: 20
    fourcc: "MJPG"
    video:
      type: "gstreamer_arecord"
      options:
        publisher_path: "/home/pi/bin/gstreamer-publisher"
        publish_backend: "ffmpeg"
        video_codec: "h264_v4l2m2m"
        target_bitrate_kbps: 1200
        audio_device: "plughw:3,0"

  - id: "rear"
    device: "/dev/v4l/by-path/...rear..."
    width: 640
    height: 360
    fps: 10
    fourcc: "MJPG"
    video:
      type: "gstreamer"
      options:
        publisher_path: "/home/pi/bin/gstreamer-publisher"
        publish_backend: "ffmpeg"
        video_codec: "h264_v4l2m2m"
        target_bitrate_kbps: 350
```

For multi-camera robots, prefer:

- `/dev/v4l/by-id/...`
- or `/dev/v4l/by-path/...`

instead of raw `/dev/video0`, `/dev/video2`, because device numbers can swap after reboot.

---

## Troubleshooting

The client now runs a startup preflight for `gstreamer` / `gstreamer_arecord` and fails fast with actionable package hints when core dependencies are missing.

Common preflight failures:

- missing `gst-inspect-1.0` or required elements (`h264parse`, `filesrc`, `v4l2src`, ...)
- missing `ffmpeg` when `publish_backend: "ffmpeg"` is selected
- missing FFmpeg encoder requested by `video_codec`

**`gstreamer-publisher is not installed`**

Install it with:

```bash
./scripts/install-gstreamer-publisher.sh
```

**`gstreamer_arecord requires the GStreamer ALSA plugin`**

Install:

```bash
sudo apt install -y gstreamer1.0-alsa
```

**I want the easiest setup**

Use:

```yaml
video:
  type: "ffmpeg"
  options: {}
```

**High CPU or unstable FPS**

- prefer `MJPG` webcams
- lower resolution before lowering bitrate
- start with `1280x720 @ 20` for front view
- use lower settings for rear cameras

**The wrong camera becomes `front` or `rear` after reboot**

Use stable paths from:

```bash
ls -l /dev/v4l/by-id /dev/v4l/by-path
```

and put those symlinks directly into `config.yaml`.
