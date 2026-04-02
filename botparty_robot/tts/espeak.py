"""eSpeak TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from .common import command_exists, run_shell, shell_quote, write_text_file


class TTSProfile(BaseTTSProfile):
    profile_name = "espeak"

    def setup(self) -> None:
        self.espeak_path = str(self.options.get("espeak_path", "espeak"))
        self.aplay_path = str(self.options.get("aplay_path", "aplay"))
        self.voice = str(self.options.get("voice", "en-us"))
        self.voice_variant = str(self.options.get("voice_variant", "m1"))
        self.speed = int(self.options.get("speed", 170))

    def can_handle(self) -> bool:
        return self.enabled and command_exists(self.espeak_path) and command_exists(self.aplay_path)

    def say(self, message: str, metadata=None) -> None:
        if not self.can_handle():
            return
        text_path = write_text_file(message)
        try:
            command = (
                f"cat {shell_quote(str(text_path))} | "
                f"{shell_quote(self.espeak_path)} -v {shell_quote(self.voice + '+' + self.voice_variant)} "
                f"-s {self.speed} --stdout | {shell_quote(self.aplay_path)} -D {shell_quote(self.playback_device)}"
            )
            run_shell(command)
        finally:
            text_path.unlink(missing_ok=True)
