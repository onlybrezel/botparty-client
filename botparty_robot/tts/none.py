"""No-op TTS profile."""

from typing import Any

from .base import BaseTTSProfile


class TTSProfile(BaseTTSProfile):
    profile_name = "none"

    def can_handle(self) -> bool:
        return False

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        del message, metadata
        return
