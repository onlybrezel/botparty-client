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
pico2wave -w /tmp/test.wav "Hello from BotParty" && aplay /tmp/test.wav
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `pico2wave_path` | string | `pico2wave` | Path to the pico2wave binary |
| `aplay_path` | string | `aplay` | Path to the aplay binary |
| `voice` | string | `en-US` | Language code |

### Available voices

Pico supports a limited set of languages:

| Code | Language |
|------|---------|
| `en-US` | English (US) |
| `en-GB` | English (UK) |
| `de-DE` | German |
| `fr-FR` | French |
| `es-ES` | Spanish |
| `it-IT` | Italian |

---

# Festival TTS

Festival is a general-purpose speech synthesis system developed at the University of Edinburgh. It produces good quality speech and supports a wide range of voices via addon packages.

```yaml
tts:
  enabled: true
  type: "festival"
  playback_device: "default"
  volume: 75
  options:
    voice: "voice_kal_diphone"
```

---

## Install

```bash
sudo apt install festival festvox-kallpc16k alsa-utils
```

Test it:

```bash
echo "Hello from BotParty" | festival --tts
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `festival_path` | string | `festival` | Path to the festival binary |
| `aplay_path` | string | `aplay` | Path to the aplay binary |
| `voice` | string | `voice_kal_diphone` | Festival voice name |

List installed voices:

```bash
festival --pipe <<< "(voice.list)"
```

---

# Amazon Polly TTS

Amazon Polly is a cloud text-to-speech service that produces highly natural speech using neural voices. It requires an AWS account and internet access.

```yaml
tts:
  enabled: true
  type: "polly"
  playback_device: "default"
  volume: 80
  options:
    region_name: "eu-central-1"
    robot_voice: "Amy"
    # Credentials (prefer env vars or IAM role over hardcoding)
    access_key: ""
    secret_key: ""
```

---

## Install

```bash
pip install boto3
sudo apt install mpg123
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `region_name` | string | `eu-central-1` | AWS region |
| `robot_voice` | string | `Amy` | Polly voice ID |
| `mpg123_path` | string | `mpg123` | Path to mpg123 binary |
| `access_key` | string | `null` | AWS access key (prefer `AWS_ACCESS_KEY_ID` env var) |
| `secret_key` | string | `null` | AWS secret key (prefer `AWS_SECRET_ACCESS_KEY` env var) |

### Preferred: environment variables

```bash
export AWS_ACCESS_KEY_ID=AKIAxxxxxxxx
export AWS_SECRET_ACCESS_KEY=xxxxxxxx
export AWS_REGION=eu-central-1
```

If running on an EC2 instance or Raspberry Pi with an IAM role attached, no credentials are needed at all — boto3 picks them up automatically.

### Popular voices

| Voice | Language | Gender |
|-------|---------|--------|
| `Amy` | en-GB | Female |
| `Joanna` | en-US | Female |
| `Matthew` | en-US | Male |
| `Brian` | en-GB | Male |
| `Vicki` | de-DE | Female |
| `Celine` | fr-FR | Female |

List all available voices:

```bash
aws polly describe-voices --region eu-central-1
```

---

# Google Cloud TTS

Google Cloud Text-to-Speech offers some of the most natural-sounding voices available, including WaveNet and Neural2 models.

```yaml
tts:
  enabled: true
  type: "google_cloud"
  playback_device: "default"
  volume: 80
  options:
    key_file: "/home/pi/google-tts.json"
    voice: "en-US-Neural2-F"
    language_code: "en-US"
```

---

## Install

```bash
pip install google-cloud-texttospeech
sudo apt install mpg123
```

Create a service account in the [Google Cloud Console](https://console.cloud.google.com), enable the Cloud Text-to-Speech API, and download the JSON key file.

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `key_file` | string | `null` | Path to service account JSON (or use `GOOGLE_APPLICATION_CREDENTIALS` env var) |
| `voice` | string | `en-US-Neural2-F` | Voice name |
| `language_code` | string | `en-US` | BCP-47 language code |
| `mpg123_path` | string | `mpg123` | Path to mpg123 binary |

Popular neural voices: `en-US-Neural2-F`, `en-US-Neural2-D`, `en-GB-Neural2-B`, `de-DE-Neural2-F`
