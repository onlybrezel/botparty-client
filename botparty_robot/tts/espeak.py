"""eSpeak TTS profile."""

from __future__ import annotations

from typing import Any

from .base import BaseTTSProfile
from .common import command_exists, run_pipeline, write_text_file


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

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        if not self.can_handle():
            return
        text_path = write_text_file(message)
        try:
            run_pipeline(
                [
                    [
                        self.espeak_path,
                        "-v",
                        self.voice + "+" + self.voice_variant,
                        "-s",
                        str(self.speed),
                        "-f",
                        str(text_path),
                        "--stdout",
                    ],
                    [self.aplay_path, "-D", self.playback_device],
                ]
            )
        finally:
            text_path.unlink(missing_ok=True)
