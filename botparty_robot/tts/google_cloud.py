"""Google Cloud Text-to-Speech profile."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .base import BaseTTSProfile
from .common import command_exists, getenv_or_option, run_shell, shell_quote, write_bytes_file


class TTSProfile(BaseTTSProfile):
    profile_name = "google_cloud"

    def setup(self) -> None:
        try:
            from google.cloud import texttospeech
            from google.oauth2 import service_account
        except ImportError:
            self.texttospeech = None
            self.credentials = None
            return

        self.texttospeech = texttospeech
        self.credentials = service_account
        self.aplay_path = str(self.options.get("aplay_path", "aplay"))
        self.voice = str(self.options.get("voice", "en-US-Neural2-F"))
        self.language_code = str(
            self.options.get("language_code", self._infer_language_code(self.voice))
        )
        self.pitch = float(self.options.get("voice_pitch", 0.0))
        self.speaking_rate = float(self.options.get("voice_speaking_rate", 1.0))
        self.ssml_enabled = bool(self.options.get("ssml_enabled", False))
        key_file = getenv_or_option(self.options, "key_file", "GOOGLE_APPLICATION_CREDENTIALS")
        if key_file:
            creds = self.credentials.Credentials.from_service_account_file(key_file)
            self.client = self.texttospeech.TextToSpeechClient(credentials=creds)
        else:
            self.client = self.texttospeech.TextToSpeechClient()

    def can_handle(self) -> bool:
        return (
            self.enabled
            and getattr(self, "texttospeech", None) is not None
            and command_exists(self.aplay_path)
        )

    def say(self, message: str, metadata=None) -> None:
        if not self.can_handle():
            return
        tts = self.texttospeech
        synthesis_input = (
            tts.SynthesisInput(ssml=f"<speak>{escape(message)}</speak>")
            if self.ssml_enabled
            else tts.SynthesisInput(text=message)
        )
        voice = tts.VoiceSelectionParams(name=self.voice, language_code=self.language_code)
        audio_config = tts.AudioConfig(
            audio_encoding=tts.AudioEncoding.LINEAR16,
            pitch=self.pitch,
            speaking_rate=self.speaking_rate,
            effects_profile_id=["small-bluetooth-speaker-class-device"],
        )
        response = self.client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
        )
        wav_path = write_bytes_file(response.audio_content, ".wav")
        try:
            run_shell(
                f"{shell_quote(self.aplay_path)} -D {shell_quote(self.playback_device)} "
                f"{shell_quote(str(wav_path))}"
            )
        finally:
            wav_path.unlink(missing_ok=True)

    def _infer_language_code(self, voice_name: str) -> str:
        parts = str(voice_name).split("-")
        if len(parts) >= 2 and parts[0] and parts[1]:
            return f"{parts[0]}-{parts[1]}"
        return "en-US"
