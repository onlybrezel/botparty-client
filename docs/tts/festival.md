# Festival TTS

Festival is a general-purpose offline speech synthesis system. The client uses the `text2wave` tool (included with Festival) to generate a WAV file which is then played back via `aplay`.

```yaml
tts:
  enabled: true
  type: "festival"
  playback_device: "default"
  volume: 75
  options: {}
```

---

## Install

```bash
sudo apt install festival alsa-utils
```

Test it:

```bash
echo "Hello from BotParty" | text2wave -o /tmp/test.wav && aplay /tmp/test.wav
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `text2wave_path` | string | `text2wave` | Path to the text2wave binary |
| `aplay_path` | string | `aplay` | Path to the aplay binary |

Voice selection is not configurable through options — Festival uses the system default voice. Install additional voice packages to change it:

```bash
# List installed voices
festival --pipe <<< "(voice.list)"

# Example additional English voice
sudo apt install festvox-rablpc16k
```

---

## Troubleshooting

**`text2wave: command not found`**

`text2wave` is part of the `festival` package:

```bash
sudo apt install festival
which text2wave
```

**No audio output**

Run `aplay -l` to list devices and set `playback_device` accordingly:

```yaml
tts:
  playback_device: "plughw:1,0"
```
