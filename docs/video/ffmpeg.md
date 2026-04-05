# FFmpeg Video Profile

The `ffmpeg` profile uses FFmpeg to capture frames from a V4L2 camera device and publish them via LiveKit. It produces the lowest latency and highest quality of all available profiles.

```yaml
video:
  type: "ffmpeg"
  options: {}
```

---

## How it works

1. FFmpeg opens the V4L2 device with `fflags nobuffer` and `flags low_delay` to minimise buffering.
2. Frames are decoded to raw RGBA and piped to stdout.
3. The client reads frames from the pipe and publishes them to the LiveKit room.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ffmpeg_path` | string | `ffmpeg` | Path to the ffmpeg binary |
| `input_driver` | string | `v4l2` | V4L2 driver name passed to `-f` |
| `thread_queue_size` | int | `64` | FFmpeg thread queue size |
| `loglevel` | string | `error` | FFmpeg log level (`error`, `warning`, `info`) |
| `target_bitrate_kbps` | int | `null` | Cap LiveKit video bitrate |

---

## Choosing a pixel format (fourcc)

Set `camera.fourcc` to match your camera's native output:

| `fourcc` value | FFmpeg input format | Notes |
|----------------|---------------------|-------|
| `MJPG` | `mjpeg` | Best choice for USB cameras — allows high FPS at high resolution |
| `YUYV` | `yuyv422` | Raw uncompressed — higher CPU load, lower resolution at 30 fps |

```bash
# Check what your camera supports
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Most modern USB webcams support MJPG at 1280x720@30fps. Switch to YUYV only if the camera does not support MJPG.

---

## With microphone audio

Use `ffmpeg_arecord` to capture and stream audio from a USB microphone or onboard sound card alongside the video.

```yaml
video:
  type: "ffmpeg_arecord"
  options:
    audio_device: "default"
    audio_sample_rate: 48000
    audio_channels: 1
```

List available ALSA capture devices:

```bash
arecord -l
```

Use the `plughw:X,Y` notation to address a specific card:

```bash
arecord -D plughw:1,0 -d 3 test.wav   # record 3 seconds to verify it works
```

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `audio_device` | string | `default` | ALSA capture device |
| `audio_sample_rate` | int | `48000` | Sample rate in Hz |
| `audio_channels` | int | `1` | 1 = mono, 2 = stereo |

---

## Troubleshooting

**"No such file or directory: /dev/video0"**

The camera is not connected or the kernel module is not loaded.

```bash
ls /dev/video*
dmesg | grep video
```

**Low frame rate / high CPU**

- Use MJPG instead of YUYV
- Reduce resolution: `width: 640, height: 480`
- Lower FPS: `fps: 15`

**"Device or resource busy"**

Another process has the camera open (another ffmpeg, guvcview, etc.). Find and kill it:

```bash
fuser /dev/video0
```
