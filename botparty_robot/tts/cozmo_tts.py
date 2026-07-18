"""Cozmo SDK-backed TTS profile."""

from __future__ import annotations

from typing import Any

from ..hardware.cozmo import get_cozmo_robot
from .base import BaseTTSProfile


class TTSProfile(BaseTTSProfile):
    profile_name = "cozmo_tts"

    def can_handle(self) -> bool:
        return self.enabled and get_cozmo_robot() is not None

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        robot = get_cozmo_robot()
        if robot is None:
            return
        robot.say_text(message).wait_for_completed()
