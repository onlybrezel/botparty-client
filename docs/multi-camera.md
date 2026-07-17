# Multi-camera

Each enabled camera has one publisher owner and one stable ID. Use `/dev/v4l/by-id/` or `/dev/v4l/by-path/` paths so devices do not swap after reboot.

```yaml
cameras:
  - id: front
    role: primary
    enabled: true
    publish_mode: always_on
    device: /dev/v4l/by-id/front-camera
    width: 1280
    height: 720
    fps: 24
  - id: rear
    role: secondary
    enabled: true
    publish_mode: always_on
    device: /dev/v4l/by-id/rear-camera
    width: 640
    height: 360
    fps: 12
audio_source_camera_id: front
```

Only `always_on` is supported. Unsupported publish modes fail configuration validation.

`audio_source_camera_id` must name an enabled camera whose profile supports audio. Without an explicit ID, the first enabled audio-capable camera is selected. Exactly one camera publishes audio.

A publisher that misses its shutdown deadline remains the owner in `failed` state. The client blocks a replacement until the old task exits, preventing duplicate device or port use. Restart budgets reset after 60 seconds of stable frames or a new media session.

The effective bitrate is `min(local_target, server_cap)`. A server cap is never exceeded.
