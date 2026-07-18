# Google Cloud TTS

Google Cloud Text-to-Speech requires an enabled Text-to-Speech API, a narrowly scoped service
account and explicit approval for cloud processing. Choose the provider region and retention terms
for the deployment before enabling it.

```yaml
tts:
  enabled: true
  type: "google_cloud"
  playback_device: "default"
  volume: 80
  options:
    key_file: "/etc/botparty/credentials/google-tts.json"
    cloud_data_processing_accepted: true
    voice: "en-US-Neural2-F"
    language_code: "en-US"
```

## Install

```bash
sudo ./scripts/install-botparty-client.sh --extras google-tts

# WAV playback via ALSA
sudo apt install alsa-utils
```

Test the library:

```bash
sudo -u botparty /opt/botparty/venv/bin/python -c \
  "from google.cloud import texttospeech; print('ok')"
```

## Google Cloud credentials

1. Create a Google Cloud project and enable the **Cloud Text-to-Speech API**.
2. Create a **Service Account** with the `Cloud Text-to-Speech User` role.
3. Download the JSON key file once, copy it into the protected service credential directory and
   remove the transport copy.

```bash
sudo install -d -m 0750 -o root -g botparty /etc/botparty/credentials
sudo install -m 0640 -o root -g botparty google-tts.json \
  /etc/botparty/credentials/google-tts.json
```

The production unit has `ProtectHome=yes`; credentials under `/home` are intentionally unavailable.

**Option A — key file in config.yaml:**

```yaml
options:
  key_file: "/etc/botparty/credentials/google-tts.json"
  cloud_data_processing_accepted: true
```

**Option B — environment variable** (credentials never touch `config.yaml`):

```bash
sudo systemctl edit botparty-robot.service
# Add under [Service]:
# Environment=GOOGLE_APPLICATION_CREDENTIALS=/etc/botparty/credentials/google-tts.json
```

When the environment variable is set you can omit `key_file` from `config.yaml`.

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `key_file` | string | — | Path to the GCP service account JSON file (or set `GOOGLE_APPLICATION_CREDENTIALS`) |
| `cloud_data_processing_accepted` | bool | `false` | Required approval for sending text to Google Cloud |
| `voice` | string | `en-US-Neural2-F` | Voice name — see voice list below |
| `language_code` | string | inferred from `voice` | Optional explicit BCP-47 language code |
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

## Troubleshooting

**No audio, no errors in log**

Run the non-moving checks; they report the missing library, playback tool, credentials or cloud
processing approval without sending text:

```bash
sudo -u botparty /opt/botparty/venv/bin/botparty-robot \
  --config /etc/botparty/config.yaml doctor
sudo -u botparty /opt/botparty/venv/bin/python -c \
  "from google.cloud import texttospeech; print('ok')"
which aplay
```
Use your actual virtualenv path if it differs.

**`DefaultCredentialsError`**

The credential file is not found or not readable by `botparty`. Check the protected path, owner and
group above. Do not make the JSON world-readable.

**`PermissionDenied` / `403`**

The service account lacks the required role. Add `Cloud Text-to-Speech User` in the Google Cloud IAM console.

Rotate service-account keys on the deployment schedule and immediately after suspected disclosure.
Install the replacement atomically with the same owner/mode, restart the service, run `doctor`, then
revoke the old key. Cloud TTS text is sent to the configured Google endpoint; document region,
processor terms and deletion/retention in the deployment compliance matrix.

**Audio plays but at wrong device**

Run `aplay -l` to list cards, then set `playback_device`:

```yaml
playback_device: "plughw:1,0"
```
