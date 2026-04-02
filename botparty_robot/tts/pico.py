"""pico2wave TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from .common import command_exists, make_temp_path, run_shell, shell_quote


class TTSProfile(BaseTTSProfile):
    profile_name = "pico"

    def setup(self) -> None:
        self.pico2wave_path = str(self.options.get("pico2wave_path", "pico2wave"))
        self.aplay_path = str(self.options.get("aplay_path", "aplay"))
        self.voice = str(self.options.get("voice", "en-US"))

    def can_handle(self) -> bool:
        return self.enabled and command_exists(self.pico2wave_path) and command_exists(self.aplay_path)

    def say(self, message: str, metadata=None) -> None:
        if not self.can_handle():
            return
        wave_path = make_temp_path(".wav")
        try:
            run_shell(
                f"{shell_quote(self.pico2wave_path)} --lang={shell_quote(self.voice)} "
                f"--wave={shell_quote(str(wave_path))} "
                f"{shell_quote(message)}"
            )
            run_shell(
                f"{shell_quote(self.aplay_path)} -D {shell_quote(self.playback_device)} "
                f"{shell_quote(str(wave_path))}"
            )
        finally:
            wave_path.unlink(missing_ok=True)
