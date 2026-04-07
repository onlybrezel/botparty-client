# Multi-Camera

BotParty can run with more than one camera. The most common setup is:

- one front camera for normal driving
- one rear camera for reversing

Single-camera robots still work exactly as before. Multi-camera is optional.

---

## When to use it

Use more than one camera when a single view is not enough:

- front + rear driving camera
- front camera + arm or gripper close-up camera
- front camera + docking camera

If your robot is simple and one camera already shows everything you need, stay with a single camera. It is easier to wire, easier to debug, and uses less CPU.

---

## How it works

Each configured camera is published as its own video track in the same BotParty room.

That means:

- viewers still open one robot page
- the client stays one process
- the frontend can show one large video and one small preview

This is better than building both cameras into one combined image because:

- the main view stays full quality
- the second view can run at lower FPS and bitrate
- switching between views does not require reconnecting the room

---

## Single camera stays the default

If you only use the normal top-level `camera` and `video` blocks, the client behaves like a normal single-camera robot:

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 30
  fourcc: "MJPG"

video:
  type: "ffmpeg"
  options: {}
```

You do not need to add anything else.

---

## Multi-camera config

To enable more than one camera, add a `cameras` list.

Example:

```yaml
camera:
  device: "/dev/video0"
  width: 1280
  height: 720
  fps: 30
  fourcc: "MJPG"

video:
  type: "ffmpeg"
  options: {}

cameras:
  - id: "front"
    label: "Front"
    role: "primary"
    enabled: true
    publish_mode: "always_on"
    device: "/dev/video0"
    width: 1280
    height: 720
    fps: 24
    fourcc: "MJPG"
    video:
      type: "ffmpeg"
      options:
        target_bitrate_kbps: 1200

  - id: "rear"
    label: "Rear"
    role: "secondary"
    enabled: true
    publish_mode: "preview_only"
    device: "/dev/video2"
    width: 640
    height: 360
    fps: 12
    fourcc: "MJPG"
    video:
      type: "ffmpeg"
      options:
        target_bitrate_kbps: 350
```

The top-level `camera` and `video` blocks still exist as defaults. Each entry in `cameras` can override them.

---

## Camera fields

| Key | Type | Description |
|-----|------|-------------|
| `id` | string | Short stable name such as `front`, `rear`, `arm` |
| `label` | string | Display name shown in the UI |
| `role` | string | Usually `primary` or `secondary` |
| `enabled` | bool | Turn one camera off without removing its config |
| `publish_mode` | string | `always_on`, `preview_only`, or `on_demand` |
| `device` | string/int | Camera device path or index |
| `width` / `height` / `fps` | int | Capture settings |
| `fourcc` | string | Usually `MJPG` for USB cameras |
| `video.type` | string | Video profile for this camera |
| `video.options` | object | Profile-specific options |

---

## Good starting values

For a front + rear robot, do not run both cameras at the same quality.

Start with:

- front camera: `1280x720` at `20-30 fps`
- rear camera: `640x360` or `640x480` at `10-15 fps`

This usually feels much better than two heavy streams because:

- the front camera stays sharp while driving
- the rear camera is still useful for reversing
- CPU load stays much lower

---

## Publish modes

### `always_on`

The camera is always published.

Use this for:

- the main driving camera
- a rear camera on stronger hardware

### `preview_only`

The camera is still published, but you should think of it as a small helper view, not a full-quality main view.

Use this for:

- rear camera
- docking camera

### `on_demand`

Reserve this for weaker hardware or more advanced setups. It is useful when the second camera should only come up in special situations.

Use this for:

- extra cameras on Raspberry Pi systems with limited headroom
- specialized cameras that are not needed all the time

---

## Viewer behavior

In the browser, the intended behavior is:

- one camera: the normal single-camera layout
- two cameras: one large main view and one small preview
- three or more cameras: one large main view and the others as small previews

Recommended default:

- `front` is the large main view
- `rear` is the small preview

The viewer can then swap them if needed.

---

## Audio

Only one camera should carry audio.

Do not try to publish microphone audio from every camera. That creates duplicate audio and makes debugging much harder.

If you need microphone audio, attach it to the main camera path.

---

## Failure behavior

Multi-camera should fail gracefully.

Expected behavior:

- if the rear camera fails, the front camera should keep streaming
- if one camera has a bad device path, the whole client should not become unusable
- if one camera restarts, the others should stay up

This is one of the main reasons BotParty publishes separate tracks instead of merging both cameras into one video.

---

## Hardware notes

### Raspberry Pi / small boards

Be careful with two cameras on small hardware.

Good rules:

- keep the second camera at lower resolution
- keep the second camera at lower FPS
- use `MJPG` on USB webcams where possible
- avoid software scaling if the camera already supports the size you want

### Two USB cameras

Two USB cameras can work, but they may fight over bandwidth on weaker systems.

If you see unstable FPS or high delay:

- lower the rear camera settings first
- check `ls /dev/video*` and make sure the devices are stable
- use `v4l2-ctl --list-formats-ext` to see what each camera supports

### CSI + USB

If you have the choice, a CSI front camera plus a small USB rear camera is often easier on the system than two heavier USB webcams.

---

## Tips

- Get one camera working first before adding the second.
- Start with the same `ffmpeg` profile on both cameras.
- Tune resolution and FPS before tuning bitrate.
- Give each camera a clear ID such as `front` and `rear`.
- Test reverse driving with the rear camera before inviting real users into the room.

---

## Troubleshooting

### Only one camera appears in the browser

Check:

```bash
ls /dev/video*
```

Then verify both configured device paths are correct.

Also check the client logs. On startup the client prints every configured camera and its device path.

### The second camera makes the first one lag

Reduce the second camera first:

```yaml
width: 640
height: 360
fps: 12
```

Then lower bitrate if needed.

### Rear camera shows up, but quality is bad

That is often okay if it is meant as a preview camera. For reversing, stable low-latency video is usually more useful than high detail.

### Camera IDs seem ignored

Use simple IDs such as:

- `front`
- `rear`
- `arm`

Avoid spaces and long names in `id`. Put the human-friendly text in `label`.

---

## Summary

If you want front + rear video, the best starting point is:

- `front` as the main high-quality camera
- `rear` as a lower-cost preview camera
- one BotParty room
- one video track per camera
- one audio source total

That gives the best balance of quality, performance, and reliability.
