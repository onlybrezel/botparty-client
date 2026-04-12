# Video Profiles

Set `video.type` in `config.yaml` to choose the pipeline.

## Available profiles

| Type | Description | Best for |
|------|-------------|---------|
| [`ffmpeg`](ffmpeg.md) | FFmpeg capture with automatic direct-publisher fallback logic | Easiest default |
| [`ffmpeg_arecord`](ffmpeg.md#with-microphone-audio) | FFmpeg + ALSA microphone with the same automatic direct-publisher logic | Single-cam with mic |
| `botparty_streamer` | Self-made direct H.264 streaming client | Manual power-user mode |
| [`ffmpeg_libcamera`](libcamera.md) | libcamera path for Pi camera modules | CSI cameras on Raspberry Pi |
| [`opencv`](opencv.md) | OpenCV fallback path | Dev/test machines |
| `none` | Disable video publishing | Control-only setups |
| `ffmpeg_hud` | FFmpeg + overlay hooks | Custom HUD setups |
| `cozmo_vid` | Cozmo video source | Cozmo robots |
| `vector_vid` | Vector video source | Vector robots |

## Recommended setup

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 25
  fourcc: "MJPG"

video:
  type: "ffmpeg"
  options:
    target_bitrate_kbps: 1200
```

`ffmpeg` auto-selects `botparty-streamer` when healthy, prefers hardware H.264 when available, updates the helper to the active version when needed, and falls back to SDK publish if direct mode is not usable.

## Multi-camera quick rule

- front camera: 720p at 20-30 fps
- rear camera: 360p or 480p at 10-15 fps

That keeps the driving view sharp while preserving CPU headroom.
