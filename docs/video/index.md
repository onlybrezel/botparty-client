# Video Profiles

The video profile controls how camera frames are captured and streamed.

Set `video.type` in `config.yaml`. All profiles share the camera settings from the `camera` block.

For multi-camera robots, the same profile types can be applied per camera through a `cameras[].video` block. The recommended architecture for that is documented in [Multi-camera design](../multi-camera.md).

---

## Available profiles

| Type | Description | Best for |
|------|-------------|---------|
| [`ffmpeg`](ffmpeg.md) | FFmpeg V4L2 capture | Default choice for most USB cameras |
| [`ffmpeg_arecord`](ffmpeg.md#with-microphone-audio) | FFmpeg + ALSA microphone | USB cameras with audio |
| [`gstreamer`](gstreamer.md) | Optional low-latency H.264 mode | Raspberry Pi 4/5 with extra helper installed |
| [`gstreamer_arecord`](gstreamer.md) | Same as above plus ALSA microphone | Raspberry Pi robots with mic audio |
| `botparty_streamer` | ffmpeg -> localhost TCP -> botparty-streamer -> LiveKit | Client-managed low-latency direct publishing |
| [`ffmpeg_libcamera`](libcamera.md) | libcamera-vid piped to FFmpeg | Raspberry Pi Camera Module |
| [`opencv`](opencv.md) | OpenCV pure Python | Simple setups, no FFmpeg |
| `none` | Disable video publishing | Audio-only or control-only setups |
| `ffmpeg_hud` | FFmpeg capture with HUD overlay support | Specialized/custom setups |
| `cozmo_vid` | Video from an attached Cozmo robot | Cozmo robots |
| `vector_vid` | Video from an attached Vector robot | Vector robots |

---

## Camera discovery

```bash
# List video devices
ls /dev/video*
v4l2-ctl --list-devices

# Check supported resolutions for /dev/video0
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Choose a resolution and format that your camera supports natively. Requesting an unsupported resolution forces a software rescale, increasing CPU usage.

---

## Recommended settings

For a simple default setup:

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 25
  fourcc: "MJPG"

video:
  type: "ffmpeg"
  options: {}
```

If you want the optional Raspberry Pi 4/5 low-latency mode with hardware H.264:

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
    video_codec: "h264_v4l2m2m"
    publish_backend: "ffmpeg"
    target_bitrate_kbps: 1200
```

That tested path is:

- `ffmpeg` reads the camera
- `h264_v4l2m2m` does the Raspberry Pi H.264 encoding
- `gstreamer-publisher` sends the already encoded stream to LiveKit

If you want microphone audio with `gstreamer_arecord`, install `gstreamer1.0-alsa` as well.

For the full setup and troubleshooting page, see [`gstreamer.md`](gstreamer.md).

Use the normal `ffmpeg` profile if you want the simplest setup. Use `gstreamer` only when you specifically want the lower-latency Raspberry Pi path.

If you want direct publishing but still keep everything inside the normal botparty-client controller lifecycle (start/restart/shutdown), use:

```yaml
video:
  type: "botparty_streamer"
  options:
    publisher_binary: "botparty-streamer"
    video_codec: "h264_v4l2m2m"   # RPi4/5 example
    target_bitrate_kbps: 1200
```

This uses short-lived publish tokens from the existing claim flow and does not require LiveKit API secrets on the client.

For a front + rear setup, do not treat both cameras equally by default. A good starting point is:

- front camera: 720p at 20-30 fps
- rear camera: 360p or 480p at 10-15 fps

That keeps the driving view sharp while preserving CPU headroom and low latency.

For lower CPU usage on Raspberry Pi 3 or older hardware:

```yaml
camera:
  device: "/dev/video0"
  width: 640
  height: 480
  fps: 24
  fourcc: "MJPG"
```

---

## LiveKit bitrate

The client applies a conservative bitrate cap automatically for low latency. You can override it explicitly with `target_bitrate_kbps` in the video options:

```yaml
video:
  type: "ffmpeg"
  options:
    target_bitrate_kbps: 800   # cap stream at 800 kbps
```
