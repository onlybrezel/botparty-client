"""Vector SDK-backed TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from ..hardware.vector import get_vector_robot


class TTSProfile(BaseTTSProfile):
    profile_name = "vector_tts"

    def can_handle(self) -> bool:
        return self.enabled and get_vector_robot() is not None

    def say(self, message: str, metadata=None) -> None:
        robot = get_vector_robot()
        if robot is None:
            return
        robot.behavior.say_text(message, duration_scalar=0.75)
