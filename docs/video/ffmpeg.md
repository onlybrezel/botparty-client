# FFmpeg Video Profile

The `ffmpeg` profile uses FFmpeg to capture frames from a V4L2 camera device and stream them through the built-in Python video path.

This is the normal default profile and the easiest place to start.

```yaml
video:
  type: "ffmpeg"
  options: {}
```

---

## How it works

1. FFmpeg opens the V4L2 device with aggressive low-latency flags such as `fflags nobuffer`, `flags low_delay`, and tiny probe sizes.
2. Frames are decoded to raw RGBA and piped to stdout.
3. The client keeps only the newest frame when the publish loop falls briefly behind, so stale video does not build up in the pipe.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `ffmpeg_path` | string | `ffmpeg` | Path to the ffmpeg binary |
| `input_driver` | string | `v4l2` | V4L2 driver name passed to `-f` |
| `input_format` | string | `auto` | Force FFmpeg input format (for example `mjpeg`, `yuyv422`) or `auto` to let FFmpeg decide |
| `thread_queue_size` | int | `2` | FFmpeg input queue size. Keep this small to avoid stale frames. |
| `analyzeduration` | int | `0` | FFmpeg probe duration in microseconds. Lower keeps startup and buffering tight. |
| `probesize` | int | `32` | FFmpeg probe size in bytes. Lower reduces startup buffering. |
| `fpsprobesize` | int | `0` | Disable extra FPS probing to start reading frames immediately. |
| `publish_fps` | float | `camera.fps` | Max frame rate sent into LiveKit. Lower this to prevent encoder backlog. |
| `loglevel` | string | `error` | FFmpeg log level (`error`, `warning`, `info`) |
| `target_bitrate_kbps` | int | `auto` | Cap LiveKit video bitrate. If unset, the client applies a conservative low-latency default based on resolution and FPS. |

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
| `audio_chunk_ms` | int | `40` | Audio chunk size pushed into LiveKit |
| `audio_queue_frames` | int | `8` | Max queued audio chunks before old audio is dropped |

For low-latency teleoperation, keep `audio_queue_frames` small. With the default `audio_chunk_ms: 40` and `audio_queue_frames: 8`, the client holds at most about 320 ms of extra audio before dropping old chunks while reducing audio scheduling pressure.

---

## Troubleshooting

**I want the optional low-latency Raspberry Pi mode**

Use:

```yaml
video:
  type: "gstreamer"
  options:
    publisher_path: "/home/pi/bin/gstreamer-publisher"
    video_codec: "h264_v4l2m2m"
    publish_backend: "ffmpeg"
    target_bitrate_kbps: 1200
```

Then install the BotParty-tested publisher binary:

```bash
./scripts/install-gstreamer-publisher.sh
```

That Raspberry Pi mode uses `ffmpeg` for capture plus `h264_v4l2m2m` for hardware H.264 encoding, then hands the stream to `gstreamer-publisher` for direct LiveKit publishing.

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

**Image has colored stripes / torn lines**

This is usually a camera pixel-format mismatch (forced `MJPG`/`YUYV` not matching what the webcam currently delivers).

Try auto format detection first:

```yaml
camera:
  fourcc: null

video:
  type: "ffmpeg"
  options:
    input_format: "auto"
```

Then list camera-supported formats and lock one explicit format only if needed:

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

**Stable FPS but 1-2 seconds behind real time**

- Keep `video.options.thread_queue_size` small (`2` is the default)
- Prefer `camera.fourcc: "MJPG"` on USB webcams
- Set `video.options.publish_fps` lower than camera FPS (for example `15` or `18`) to avoid sender-side encoder queue buildup
- Lower resolution before lowering bitrate; CPU pressure often shows up as delay before it shows up as dropped FPS

**"Device or resource busy"**

Another process has the camera open (another ffmpeg, guvcview, etc.). Find and kill it:

```bash
fuser /dev/video0
```
