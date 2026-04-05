# libcamera Video Profile

The `ffmpeg_libcamera` profile is for **Raspberry Pi Camera Module** boards (v2, v3, HQ) on systems using the modern `libcamera` stack (Raspberry Pi OS Bullseye and newer).

```yaml
video:
  type: "ffmpeg_libcamera"
  options: {}
```

---

## How it works

`libcamera-vid` captures raw YUV420 frames and pipes them directly into FFmpeg, which converts them to RGBA for LiveKit. This avoids the V4L2 compatibility layer and gives better performance on recent Pi firmware.

```
libcamera-vid -t 0 --codec yuv420 -o - | ffmpeg -f rawvideo ... pipe:1
```

---

## Requirements

The libcamera stack must be installed and the camera must be enabled:

```bash
# Enable the camera interface
sudo raspi-config   # Interface Options → Legacy Camera → Disable
                    # (libcamera does NOT need the legacy interface)

# Verify camera is detected
libcamera-hello --list-cameras

# Test a capture
libcamera-vid -t 5000 --width 1280 --height 720 --framerate 30 --codec yuv420 -o test.yuv
```

If `libcamera-hello` shows no cameras, check the ribbon cable connection and ensure the kernel overlay is configured in `/boot/config.txt` (e.g. `dtoverlay=imx219` for Camera Module v2).

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `libcamera_path` | string | `libcamera-vid` | Path to the libcamera-vid binary |
| `ffmpeg_path` | string | `ffmpeg` | Path to the ffmpeg binary |
| `target_bitrate_kbps` | int | `null` | Cap LiveKit video bitrate |

The resolution and FPS come from the `camera` block:

```yaml
camera:
  device: "/dev/video0"   # ignored by libcamera profile
  width: 1920
  height: 1080
  fps: 30
```

> The `camera.device` path is not used by this profile — libcamera selects the camera automatically.

---

## Camera Module versions

| Module | Sensor | Max resolution | Overlay |
|--------|--------|----------------|---------|
| v1 | OmniVision OV5647 | 2592x1944 | `dtoverlay=ov5647` |
| v2 | Sony IMX219 | 3280x2464 | `dtoverlay=imx219` |
| v3 | Sony IMX708 | 4608x2592 | `dtoverlay=imx708` |
| HQ | Sony IMX477 | 4056x3040 | `dtoverlay=imx477` |

For streaming, 1280x720@30fps or 1920x1080@30fps are the most practical resolutions.

---

## Troubleshooting

**"libcamera-vid: command not found"**

```bash
sudo apt install libcamera-apps
```

**Camera not detected by libcamera but works with V4L2**

Your system may still be on the legacy camera stack. Check:

```bash
vcgencmd get_camera    # legacy: "supported=1 detected=1"
```

If the legacy stack is active and you want to keep using it, use the `ffmpeg` profile with `fourcc: MJPG` instead.

**Low framerate on Raspberry Pi 3**

Reduce resolution and fps:

```yaml
camera:
  width: 640
  height: 480
  fps: 15
```
