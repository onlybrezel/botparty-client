"""Dynamic custom TTS loader."""

from __future__ import annotations

import importlib

from .base import BaseTTSProfile


class TTSProfile(BaseTTSProfile):
    profile_name = "custom"

    def __init__(self, config) -> None:
        super().__init__(config)
        target = self.options.get("class")
        if not isinstance(target, str) or "." not in target:
            raise ValueError("tts.options.class must be a dotted path like my_robot.tts.MyTTS")
        module_name, class_name = target.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        self.inner = cls(config)

    def setup(self) -> None:
        setup = getattr(self.inner, "setup", None)
        if callable(setup):
            setup()

    def can_handle(self) -> bool:
        return bool(getattr(self.inner, "can_handle", lambda: True)())

    def say(self, message: str, metadata=None) -> None:
        self.inner.say(message, metadata)

    def mute(self) -> None:
        if hasattr(self.inner, "mute"):
            self.inner.mute()

    def unmute(self) -> None:
        if hasattr(self.inner, "unmute"):
            self.inner.unmute()

    def set_volume(self, level: int) -> None:
        if hasattr(self.inner, "set_volume"):
            self.inner.set_volume(level)
