# eSpeak TTS

eSpeak is a free, compact, offline text-to-speech engine available for Raspberry Pi and most Linux platforms. The voice sounds robotic, which many robot builders consider part of the charm.

```yaml
tts:
  enabled: true
  type: "espeak"
  playback_device: "default"
  volume: 75
  options:
    voice: "en-us"
    voice_variant: "m1"
    speed: 165
```

---

## Install

```bash
sudo apt install espeak alsa-utils
```

Test it:

```bash
echo "Hello from your robot" | espeak -v en-us+m1 -s 165 --stdout | aplay
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `espeak_path` | string | `espeak` | Path to the espeak binary |
| `aplay_path` | string | `aplay` | Path to the aplay binary |
| `voice` | string | `en-us` | eSpeak voice/language |
| `voice_variant` | string | `m1` | Voice variant appended with `+` (e.g. `m1`, `f1`, `croak`) |
| `speed` | int | `170` | Words per minute |

### Available voices

List all installed voices:

```bash
espeak --voices
```

Common values: `en-us`, `en-gb`, `de`, `fr`, `es`, `it`, `pt`, `nl`, `ru`, `zh`

### Voice variants

Variants modify the character of the voice:

| Variant | Description |
|---------|-------------|
| `m1`–`m7` | Male variants |
| `f1`–`f4` | Female variants |
| `croak` | Deep, croaky |
| `whisper` | Soft whisper |

```yaml
options:
  voice: "en-gb"
  voice_variant: "f3"
  speed: 150
```

---

## Troubleshooting

**No audio output**

Test aplay directly:

```bash
aplay -D default /usr/share/sounds/alsa/Front_Left.wav
```

If no sound, check your ALSA default device:

```bash
aplay -l
```

Then set `playback_device` to match your output card.
