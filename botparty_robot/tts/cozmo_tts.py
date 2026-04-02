"""Cozmo SDK-backed TTS profile."""

from __future__ import annotations

from .base import BaseTTSProfile
from ..hardware.cozmo import get_cozmo_robot


class TTSProfile(BaseTTSProfile):
    profile_name = "cozmo_tts"

    def can_handle(self) -> bool:
        return self.enabled and get_cozmo_robot() is not None

    def say(self, message: str, metadata=None) -> None:
        robot = get_cozmo_robot()
        if robot is None:
            return
        robot.say_text(message).wait_for_completed()
