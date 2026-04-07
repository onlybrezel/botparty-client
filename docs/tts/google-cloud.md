# Google Cloud TTS

Google Cloud Text-to-Speech produces high-quality neural voices in 40+ languages. It requires a Google Cloud project with the Text-to-Speech API enabled and a service account JSON key.

```yaml
tts:
  enabled: true
  type: "google_cloud"
  playback_device: "default"
  volume: 80
  options:
    key_file: "/home/pi/gcp-tts.json"
    voice: "en-US-Neural2-F"
```

---

## Install

```bash
# Inside your botparty-client virtualenv
pip install google-cloud-texttospeech

# WAV playback via ALSA
sudo apt install alsa-utils
```

Test the library:

```bash
python3 -c "from google.cloud import texttospeech; print('ok')"
```

---

## Google Cloud credentials

1. Create a Google Cloud project and enable the **Cloud Text-to-Speech API**.
2. Create a **Service Account** with the `Cloud Text-to-Speech User` role.
3. Download the JSON key file and copy it to the Pi.

**Option A — key file in config.yaml:**

```yaml
options:
  key_file: "/home/pi/gcp-tts.json"
```

**Option B — environment variable** (credentials never touch `config.yaml`):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/home/pi/gcp-tts.json"
```

When the environment variable is set you can omit `key_file` from `config.yaml`.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `key_file` | string | — | Path to the GCP service account JSON file (or set `GOOGLE_APPLICATION_CREDENTIALS`) |
| `voice` | string | `en-US-Neural2-F` | Voice name — see voice list below |
| `voice_pitch` | float | `0.0` | Pitch adjustment in semitones (-20.0 to +20.0) |
| `voice_speaking_rate` | float | `1.0` | Speech rate (0.25 to 4.0; 1.0 = normal) |
| `ssml_enabled` | bool | `false` | Wrap message in `<speak>` SSML tags before synthesis |
| `aplay_path` | string | `aplay` | Path to the aplay binary |

### Selecting a voice

List available voices for a language:

```bash
gcloud text-to-speech voices list --language-code=en-US
```

Or browse the [Google Cloud voice list](https://cloud.google.com/text-to-speech/docs/voices) in the docs.

Common voices:

| Voice | Language | Type |
|-------|----------|------|
| `en-US-Neural2-F` | English (US) — Female | Neural |
| `en-US-Neural2-D` | English (US) — Male | Neural |
| `en-GB-Neural2-A` | English (UK) — Female | Neural |
| `de-DE-Neural2-A` | German — Female | Neural |
| `fr-FR-Neural2-A` | French — Female | Neural |

---

## Troubleshooting

**No audio, no errors in log**

`can_handle()` returns `False` silently when the library is missing. Check:

```bash
.venv/bin/python -c "from google.cloud import texttospeech; print('ok')"
which aplay
```
Use your actual virtualenv path if it differs.

**`DefaultCredentialsError`**

The credential file is not found. Make sure `key_file` points to the correct path, or set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable.

**`PermissionDenied` / `403`**

The service account lacks the required role. Add `Cloud Text-to-Speech User` in the Google Cloud IAM console.

**Audio plays but at wrong device**

Run `aplay -l` to list cards, then set `playback_device`:

```yaml
playback_device: "plughw:1,0"
```
