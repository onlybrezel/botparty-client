# TTS Profiles

When text-to-speech is enabled, chat messages typed by viewers are spoken through the robot's speaker in real time.

Enable TTS and pick an engine in `config.yaml`:

```yaml
tts:
  enabled: true
  type: "espeak"          # see available engines below
  playback_device: "default"
  volume: 75
  filter_urls: true
  allow_anonymous: true
  options: {}             # engine-specific options
```

---

## Available engines

| Type | Quality | Requires | Internet |
|------|---------|---------|---------|
| [`none`](#none) | — | — | No |
| [`espeak`](espeak.md) | Basic robotic | `espeak` package | No |
| [`pico`](pico.md) | Natural offline | `libttspico-utils` | No |
| [`festival`](festival.md) | Natural offline | `festival` package | No |
| [`polly`](polly.md) | High quality cloud | `boto3` + AWS account | Yes |
| [`google_cloud`](google-cloud.md) | High quality cloud | `google-cloud-texttospeech` + GCP account | Yes |
| `custom` | Your own Python class | Importable class in `tts.options.class` | Depends |
| `espeak_loop` | Legacy alias for `espeak` | Same as `espeak` | No |
| `cozmo_tts` | Speak through an attached Cozmo robot | `cozmo[camera]` | No |
| `vector_tts` | Speak through an attached Vector robot | `anki_vector` | No |

---

## Playback device

`playback_device` is passed to the ALSA `aplay`/`mpg123` command.

```bash
# List available playback devices
aplay -l

# Test a device
speaker-test -D plughw:1,0 -t wav -c 1
```

Common values:

| Value | Meaning |
|-------|---------|
| `default` | ALSA default output |
| `Headphones` | Raspberry Pi 3.5 mm headphone jack |
| `plughw:1,0` | Card 1, device 0 (use `aplay -l` to find the right numbers) |
| `hw:0,0` | Raw hardware access to card 0, device 0 |

---

## Filtering messages

| Setting | Effect |
|---------|--------|
| `filter_urls: true` | Skip messages that contain `http://...` links |
| `allow_anonymous: false` | Only speak messages from logged-in users |
| `blocked_senders: ["troll1", "spammer"]` | Silence specific usernames permanently |

---

## Volume control

The volume (0–100) is applied via `amixer` to the ALSA device at startup. You can also change it at runtime via TTS control commands such as `tts:volume`.

---

## none

Disables TTS entirely. Use this as the safe default when you do not have a speaker or do not want audio output.

```yaml
tts:
  enabled: false
  type: "none"
```
