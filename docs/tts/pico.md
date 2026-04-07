# SVOX Pico TTS

SVOX Pico produces noticeably more natural speech than eSpeak while still running fully offline. It is a good choice for Raspberry Pi robots where you want a pleasant voice without cloud dependencies.

```yaml
tts:
  enabled: true
  type: "pico"
  playback_device: "default"
  volume: 80
  options:
    voice: "en-US"
```

---

## Install

```bash
sudo apt install libttspico-utils alsa-utils
```

Test it:

```bash
pico2wave --lang=en-US --wave=/tmp/test.wav "Hello from BotParty" && aplay /tmp/test.wav
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `pico2wave_path` | string | `pico2wave` | Path to the pico2wave binary |
| `aplay_path` | string | `aplay` | Path to the aplay binary |
| `voice` | string | `en-US` | Language code passed to `--lang` |

### Available voices

Pico supports a fixed set of languages:

| Code | Language |
|------|----------|
| `en-US` | English (US) |
| `en-GB` | English (UK) |
| `de-DE` | German |
| `fr-FR` | French |
| `es-ES` | Spanish |
| `it-IT` | Italian |

---

## Troubleshooting

**`pico2wave: command not found`**

Install the package:

```bash
sudo apt install libttspico-utils
```

**No audio output**

Check your ALSA device:

```bash
aplay -l
```

Then set `playback_device` to match, for example `plughw:1,0`.
