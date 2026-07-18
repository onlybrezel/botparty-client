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
| `cameras[]` | `publish_mode` | `always_on` or policy-selected `on_demand` secondary |
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
| `ota` | enabled, manifest/key/state paths | Server-triggered signed A/B updates; standard channel is prefilled and disabled |

The complete, executable example is [config.example.yaml](../config.example.yaml). CI loads this file through the same Pydantic schema as production.

Export the editor schema with
`botparty-robot config schema --output robot-config.schema.json`. It includes every built-in
hardware, video and TTS options model with ranges and closed-key validation. Shell snippets are
available from `botparty-robot completion bash`, `zsh` or `fish`. The generated adapter registry at
[`generated/adapter-inventory.json`](generated/adapter-inventory.json) binds commands, motion,
support level, dependencies, option schemas and HIL evidence to the implementation.

OTA does not poll for releases. An authorized `update_client` action from the BotParty server
downloads the current signed manifest, installs the verified bundle in the inactive slot and
restarts into it. The standard manifest URL, public-key path and state directory are prefilled;
set `ota.enabled: true` only after the matching Ed25519 public key exists at the configured path.
`BOTPARTY_OTA_MANIFEST_URL`, `BOTPARTY_OTA_PUBLIC_KEY_FILE` and `BOTPARTY_OTA_STATE_DIR` can override
the three values for a service installation or private release channel.

## Profile registries

Hardware: `none`, `auto`, `custom`, `l298n`, `adafruit_pwm`, `motor_hat`, `serial_board`, `mqtt_pub`, `pololu`, `mc33926`, `mdd10`, `motozero`, `thunderborg`, `gopigo2`, `gopigo3`, `megapi_board`, `telly`, `max7219`, `cozmo`, `vector`, `owi_arm`, `maestro_servo`, `navq`.

Video: `none`, `ffmpeg`, `ffmpeg_arecord`, `ffmpeg_hud`, `ffmpeg_libcamera`, `botparty_streamer`, `opencv`, `cozmo_vid`, `vector_vid`.

TTS: `none`, `espeak`, `pico`, `festival`, `polly`, `google_cloud`, `custom`, `cozmo_tts`, `vector_tts`.

The former `espeak_loop` alias was removed in 0.2.0. Use `tts.type: espeak`; playback behavior is
unchanged.

## Migrations

The loader maps the legacy `camera.pipeline` value to a video profile and accepts known profile
aliases with a warning through 2026-09-01. The nonfunctional `emergency_stop_pin` and
`preview_only` options are removed. Add a physical stop in the motor power circuit; it is
independent of software and network state.
