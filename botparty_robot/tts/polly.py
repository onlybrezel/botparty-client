"""Amazon Polly TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from .common import command_exists, getenv_or_option, run_shell, shell_quote, write_bytes_file


class TTSProfile(BaseTTSProfile):
    profile_name = "polly"

    def setup(self) -> None:
        try:
            import boto3
        except ImportError:
            self.boto3 = None
            return
        self.boto3 = boto3
        self.mpg123_path = str(self.options.get("mpg123_path", "mpg123"))
        self.voice = str(self.options.get("robot_voice", "Amy"))
        self.region_name = getenv_or_option(self.options, "region_name", "AWS_REGION", "eu-central-1")
        access_key = getenv_or_option(self.options, "access_key", "AWS_ACCESS_KEY_ID")
        secret_key = getenv_or_option(self.options, "secret_key", "AWS_SECRET_ACCESS_KEY")
        session_kwargs = {"region_name": self.region_name}
        if access_key and secret_key:
            session_kwargs["aws_access_key_id"] = access_key
            session_kwargs["aws_secret_access_key"] = secret_key
        self.client = self.boto3.client("polly", **session_kwargs)

    def can_handle(self) -> bool:
        return (
            self.enabled
            and getattr(self, "boto3", None) is not None
            and command_exists(self.mpg123_path)
        )

    def say(self, message: str, metadata=None) -> None:
        if not self.can_handle():
            return
        response = self.client.synthesize_speech(
            OutputFormat="mp3",
            VoiceId=self.voice,
            Text=message,
        )
        if "AudioStream" not in response:
            return
        mp3_path = write_bytes_file(response["AudioStream"].read(), ".mp3")
        try:
            run_shell(
                f"{shell_quote(self.mpg123_path)} -a {shell_quote(self.playback_device)} "
                f"-q {shell_quote(str(mp3_path))}"
            )
        finally:
            mp3_path.unlink(missing_ok=True)
