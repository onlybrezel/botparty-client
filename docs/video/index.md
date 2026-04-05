# Video Profiles

The video profile controls how camera frames are captured and published to the LiveKit room.

Set `video.type` in `config.yaml`. All profiles share the camera settings from the `camera` block.

---

## Available profiles

| Type | Description | Best for |
|------|-------------|---------|
| [`ffmpeg`](ffmpeg.md) | FFmpeg V4L2 capture | USB cameras on Linux |
| [`ffmpeg_arecord`](ffmpeg.md#with-microphone-audio) | FFmpeg + ALSA microphone | USB cameras with audio |
| [`ffmpeg_libcamera`](libcamera.md) | libcamera-vid piped to FFmpeg | Raspberry Pi Camera Module |
| [`opencv`](opencv.md) | OpenCV pure Python | Simple setups, no FFmpeg |

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

For a USB webcam at 720p:

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 30
  fourcc: "MJPG"      # most USB cameras support MJPG at higher resolutions

video:
  type: "ffmpeg"
  options: {}
```

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

The client automatically lets LiveKit negotiate the video bitrate. You can cap it by setting `target_bitrate_kbps` in the video options:

```yaml
video:
  type: "ffmpeg"
  options:
    target_bitrate_kbps: 800   # cap stream at 800 kbps
```
