# Amazon Polly TTS

Amazon Polly is a cloud text-to-speech service that produces high-quality, natural-sounding voices in dozens of languages. It requires an AWS account and internet access.

```yaml
tts:
  enabled: true
  type: "polly"
  playback_device: "default"
  volume: 80
  options:
    region_name: "eu-central-1"
    robot_voice: "Amy"
    access_key: "YOUR_AWS_ACCESS_KEY_ID"
    secret_key: "YOUR_AWS_SECRET_ACCESS_KEY"
```

---

## Install

Install the AWS SDK and an MP3 player:

```bash
# Inside your botparty-client virtualenv
pip install boto3

# MP3 playback
sudo apt install mpg123
```

Test boto3 is available:

```bash
python3 -c "import boto3; print('ok')"
```

---

## AWS credentials

You need an IAM user with the `AmazonPollyReadOnlyAccess` policy (or at minimum `polly:SynthesizeSpeech`).

**Option A – in config.yaml** (simple, good for a single robot):

```yaml
options:
  access_key: "AKIAXXXXXXXXXXXXXXXX"
  secret_key: "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

**Option B – environment variables** (more secure, credentials never touch the config file):

```bash
export AWS_ACCESS_KEY_ID="AKIAXXXXXXXXXXXXXXXX"
export AWS_SECRET_ACCESS_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export AWS_REGION="eu-central-1"
```

When environment variables are set, you can omit `access_key` and `secret_key` from `config.yaml`.

**Option C – AWS credentials file** (`~/.aws/credentials`):

```ini
[default]
aws_access_key_id = AKIAXXXXXXXXXXXXXXXX
aws_secret_access_key = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

## Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `region_name` | string | `eu-central-1` | AWS region (pick one close to your robot) |
| `robot_voice` | string | `Amy` | Polly voice ID |
| `access_key` | string | — | AWS access key ID (or use env var) |
| `secret_key` | string | — | AWS secret access key (or use env var) |
| `mpg123_path` | string | `mpg123` | Path to the mpg123 binary |

### Regions

Pick a region close to your robot to reduce latency:

| Region | Code |
|--------|------|
| Europe (Frankfurt) | `eu-central-1` |
| Europe (Ireland) | `eu-west-1` |
| US East (N. Virginia) | `us-east-1` |
| US West (Oregon) | `us-west-2` |
| Asia Pacific (Tokyo) | `ap-northeast-1` |

### Available voices

List all voices for a region:

```bash
aws polly describe-voices --region eu-central-1 --query 'Voices[*].[Id,LanguageCode,Gender]' --output table
```

Common voices:

| Voice | Language | Gender |
|-------|----------|--------|
| `Amy` | en-GB | Female |
| `Brian` | en-GB | Male |
| `Joanna` | en-US | Female |
| `Matthew` | en-US | Male |
| `Marlene` | de-DE | Female |
| `Hans` | de-DE | Male |
| `Celine` | fr-FR | Female |
| `Conchita` | es-ES | Female |

This client currently uses Polly's default synthesis engine. There is no separate `engine` option in `config.yaml`.

---

## Troubleshooting

**No audio, no errors in log**

`can_handle()` returns `False` silently when a dependency is missing. Check:

```bash
# boto3 installed in the right env?
.venv/bin/python -c "import boto3; print('ok')"

# mpg123 installed?
which mpg123
```
Use your actual virtualenv path if it differs.

**`NoCredentialsError` or `InvalidClientTokenId`**

Your credentials are wrong or missing. Double-check `access_key` / `secret_key`, or set the environment variables.

**`AuthFailure` / `AccessDeniedException`**

The IAM user is missing the `AmazonPollyReadOnlyAccess` policy. Add it in the AWS IAM console.

**Audio plays but at wrong device**

Run `aplay -l` to list cards, then set `playback_device` to match:

```yaml
playback_device: "plughw:1,0"
```

**Latency is high**

Polly synthesis typically adds 300-600 ms. If latency matters, switch to an offline engine like `pico` or `espeak`.
