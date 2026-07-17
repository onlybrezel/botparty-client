# Configuration reference

Validate every change before restart:

```bash
botparty-robot --config config.yaml config validate
```

Unknown keys, invalid ranges, duplicate camera IDs, unsupported profiles and unsafe URLs are rejected. `BOTPARTY_CLAIM_TOKEN` overrides the file value. Runtime device identity lives in the state directory, not beside the current working directory.

## Options matrix

| Section | Options | Contract |
|---|---|---|
| `server` | `api_url`, `livekit_url`, `claim_token` | Required claim transport; HTTPS/WSS outside loopback |
| `server` | `allow_insecure_dev_transport` | Permits HTTP/WS only for loopback development |
| `server` | `report_capabilities_in_claim` | Off by default until the platform claim schema supports manifests |
| `camera` | `device`, `width`, `height`, `fps` | Base capture device and bounded dimensions/rate |
| `camera` | `backend`, `fourcc`, `buffer_size`, `warmup_frames` | Capture tuning; FOURCC is exactly four characters |
| `video` | `type`, `options` | Registered video adapter and adapter-specific options |
| `cameras[]` | `id`, `label`, `role`, `enabled` | Up to eight unique cameras; at most one enabled primary |
| `cameras[]` | camera overrides, `video` | Per-camera overrides of the base blocks |
| `cameras[]` | `publish_mode` | Only `always_on` is supported |
| root | `audio_source_camera_id` | Enabled audio-capable camera ID |
| `hardware` | `type`, `options` | Registered hardware adapter and adapter-specific options |
| `tts` | engine, volume, sender/content/rate/budget/time limits | Disabled by default; cloud engines also require explicit consent |
| `safety` | `max_run_time_ms` | Monotonic movement deadline, 100-10000 ms |
| `safety` | `command_ttl_ms` | Maximum remote command age, 100-30000 ms |
| `safety` | `stop_timeout_ms` | Adapter stop deadline, 50-5000 ms |
| `safety` | `command_queue_size` | Bounded queue, 4-1024 entries |
| `safety` | `require_media_for_motion` | Reject movement while required media is degraded |
| `state` | `directory`, `device_key_file` | Private, owner-checked device identity storage |
| `diagnostics` | upload flag, buffer, batch, retention | Opt-in redacted support log upload |
| `telemetry` | operational/product flags | Both off by default; product events contain only allowlisted milestones |
| `ota` | enabled, manifest/key/state paths | Signed transactional A/B updates; all paths required when enabled |

The complete, executable example is [config.example.yaml](../config.example.yaml). CI loads this file through the same Pydantic schema as production.

## Profile registries

Hardware: `none`, `auto`, `custom`, `l298n`, `adafruit_pwm`, `motor_hat`, `serial_board`, `mqtt_pub`, `pololu`, `mc33926`, `mdd10`, `motozero`, `thunderborg`, `gopigo2`, `gopigo3`, `megapi_board`, `telly`, `max7219`, `cozmo`, `vector`, `owi_arm`, `maestro_servo`, `navq`.

Video: `none`, `ffmpeg`, `ffmpeg_arecord`, `ffmpeg_hud`, `ffmpeg_libcamera`, `botparty_streamer`, `opencv`, `cozmo_vid`, `vector_vid`.

TTS: `none`, `espeak`, `pico`, `festival`, `polly`, `google_cloud`, `custom`, `cozmo_tts`, `vector_tts`; `espeak_loop` remains a documented migration alias.

## Migrations

The loader maps the legacy `camera.pipeline` value to a video profile and accepts known profile aliases with a warning. The nonfunctional `emergency_stop_pin`, `preview_only` and `on_demand` options are removed. Add a physical stop in the motor power circuit; it is independent of software and network state.
