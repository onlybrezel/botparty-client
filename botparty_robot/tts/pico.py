"""pico2wave TTS profile."""

from __future__ import annotations

from typing import Any

from .base import BaseTTSProfile
from .common import command_exists, make_temp_path, run_process


class TTSProfile(BaseTTSProfile):
    profile_name = "pico"

    def setup(self) -> None:
        self.pico2wave_path = str(self.options.get("pico2wave_path", "pico2wave"))
        self.aplay_path = str(self.options.get("aplay_path", "aplay"))
        self.voice = str(self.options.get("voice", "en-US"))

    def can_handle(self) -> bool:
        return (
            self.enabled and command_exists(self.pico2wave_path) and command_exists(self.aplay_path)
        )

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.can_handle():
            return
        wave_path = make_temp_path(".wav")
        try:
            run_process(
                [
                    self.pico2wave_path,
                    f"--lang={self.voice}",
                    f"--wave={wave_path}",
                    message,
                ]
            )
            if self.operation_is_active():
                run_process([self.aplay_path, "-D", self.playback_device, str(wave_path)])
        finally:
            wave_path.unlink(missing_ok=True)
