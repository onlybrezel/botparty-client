"""Festival TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from .common import command_exists, run_shell, shell_quote, write_text_file


class TTSProfile(BaseTTSProfile):
    profile_name = "festival"

    def setup(self) -> None:
        self.text2wave_path = str(self.options.get("text2wave_path", "text2wave"))
        self.aplay_path = str(self.options.get("aplay_path", "aplay"))

    def can_handle(self) -> bool:
        return self.enabled and command_exists(self.text2wave_path) and command_exists(self.aplay_path)

    def say(self, message: str, metadata=None) -> None:
        if not self.can_handle():
            return
        text_path = write_text_file(message)
        wav_path = text_path.with_suffix(".wav")
        try:
            run_shell(
                f"{shell_quote(self.text2wave_path)} -o {shell_quote(str(wav_path))} "
                f"{shell_quote(str(text_path))}"
            )
            run_shell(
                f"{shell_quote(self.aplay_path)} -D {shell_quote(self.playback_device)} "
                f"{shell_quote(str(wav_path))}"
            )
        finally:
            text_path.unlink(missing_ok=True)
            wav_path.unlink(missing_ok=True)
