"""Dynamic custom TTS loader."""

from __future__ import annotations

import importlib
import importlib.util
import os
import stat
import sys
from pathlib import Path
from typing import Any

from ..config import RobotConfig
from ..device_state import DeviceStateError, validate_trusted_code_file
from .base import BaseTTSProfile


class TTSProfile(BaseTTSProfile):
    profile_name = "custom"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        target = self.options.get("class")
        if not isinstance(target, str) or "." not in target:
            raise ValueError("tts.options.class must be a dotted path like my_robot.tts.MyTTS")
        module_name, class_name = target.rsplit(".", 1)
        expected = validate_custom_tts_module(config, module_name)
        module = importlib.import_module(module_name)
        if expected is not None:
            loaded_path = Path(str(getattr(module, "__file__", ""))).absolute()
            final = validate_trusted_code_file(loaded_path, owner_uid=expected.st_uid)
            if (final.st_dev, final.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError("custom TTS module changed while it was imported")
        cls = getattr(module, class_name)
        self.inner = cls(config)

    def setup(self) -> None:
        setup = getattr(self.inner, "setup", None)
        if callable(setup):
            setup()

    def can_handle(self) -> bool:
        return self.enabled and bool(getattr(self.inner, "can_handle", lambda: True)())

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        self.inner.say(message, metadata)

    def mute(self) -> None:
        super().mute()
        if hasattr(self.inner, "mute"):
            self.inner.mute()

    def unmute(self) -> None:
        super().unmute()
        if hasattr(self.inner, "unmute"):
            self.inner.unmute()

    def set_volume(self, level: int) -> None:
        super().set_volume(level)
        if hasattr(self.inner, "set_volume"):
            self.inner.set_volume(self.volume)


def validate_custom_tts_module(
    config: RobotConfig,
    module_name: str | None = None,
) -> os.stat_result | None:
    """Resolve and validate production custom TTS code without importing it."""

    source_path = config._source_path
    production = False
    if source_path is not None:
        try:
            production = source_path.lstat().st_uid == 0
        except OSError as exc:
            raise ValueError("cannot inspect the custom TTS production config") from exc
    if not production:
        return None
    if module_name is None:
        target = config.tts.options.get("class")
        if not isinstance(target, str) or "." not in target:
            raise ValueError("tts.options.class must be a dotted path like my_robot.tts.MyTTS")
        module_name = target.rsplit(".", 1)[0]
    loaded = sys.modules.get(module_name)
    origin = getattr(loaded, "__file__", None) if loaded is not None else None
    if not origin:
        try:
            spec = importlib.util.find_spec(module_name)
        except (ImportError, ModuleNotFoundError, ValueError) as exc:
            raise ValueError("custom TTS module cannot be resolved") from exc
        origin = spec.origin if spec is not None else None
    if not origin or origin in {"built-in", "frozen"}:
        raise ValueError("custom TTS module requires a root-controlled module file")
    path = Path(origin).absolute()
    try:
        metadata = validate_trusted_code_file(path, owner_uid=0)
    except DeviceStateError as exc:
        raise ValueError("custom TTS module is not root-controlled") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("custom TTS module is not a regular file")
    return metadata
