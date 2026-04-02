"""No-op TTS profile."""

from .base import BaseTTSProfile


class TTSProfile(BaseTTSProfile):
    profile_name = "none"

    def can_handle(self) -> bool:
        return False

    def say(self, message: str, metadata=None) -> None:
        return
