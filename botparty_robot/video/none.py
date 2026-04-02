"""Disabled video profile."""

from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "none"

    def capture_mode(self) -> str:
        return "none"

