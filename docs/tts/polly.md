# Amazon Polly TTS

Polly sends approved TTS text to the configured AWS region. Enable it only after recording the
provider, region, retention and legal basis for the deployment.

## Install

```bash
pip install 'botparty-robot[polly]'
sudo apt install mpg123
```

Use an IAM principal restricted to `polly:SynthesizeSpeech`. Supply credentials through the
service secret store or root-owned files; do not put them in `config.yaml`.

```yaml
tts:
  enabled: true
  type: polly
  playback_device: default
  volume: 80
  max_characters: 300
  rate_limit_count: 5
  rate_limit_window_sec: 60
  daily_character_budget: 20000
  operation_timeout_sec: 20
  options:
    region_name: eu-central-1
    robot_voice: Amy
    cloud_data_processing_accepted: true
    access_key_file: /run/credentials/botparty-robot.service/aws-access-key
    secret_key_file: /run/credentials/botparty-robot.service/aws-secret-key
```

The standard AWS environment variables and credentials file also work. The service account must
be the only reader. `cloud_data_processing_accepted` is mandatory; without it no synthesis request
is sent.

## Options

| Option | Default | Contract |
|---|---|---|
| `region_name` | `eu-central-1` | AWS processing region |
| `robot_voice` | `Amy` | Polly voice ID |
| `cloud_data_processing_accepted` | `false` | Explicit provider approval |
| `access_key_file`, `secret_key_file` | none | Credential files; environment credentials remain supported |
| `mpg123_path` | `mpg123` | Local player binary |
| `output_module` | automatic | `pulse` or `alsa` |

List voices with:

```bash
aws polly describe-voices --region eu-central-1 \
  --query 'Voices[*].[Id,LanguageCode,Gender]' --output table
```

## Troubleshooting

Run `botparty-robot --config /etc/botparty/config.yaml doctor` first. A missing Python package,
player binary or device permission is reported without sending text or creating cloud cost.

`NoCredentialsError` means the service account cannot read configured credentials.
`AccessDeniedException` means the IAM policy does not permit `polly:SynthesizeSpeech` in the
selected region. For device selection, use `aplay -l` and set `playback_device`, for example
`plughw:1,0`.
