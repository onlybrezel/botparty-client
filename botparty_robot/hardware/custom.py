"""Dynamic custom adapter loader."""

from __future__ import annotations

import importlib
import logging
from typing import Any

from .base import BaseHardware

logger = logging.getLogger("botparty.hardware.custom")


class HardwareAdapter(BaseHardware):
    profile_name = "custom"
    description = "Load a custom adapter class from a dotted Python path"

    def __init__(self, config) -> None:
        super().__init__(config)
        target = self.options.get("class")
        if not isinstance(target, str) or "." not in target:
            raise ValueError("hardware.options.class must be a dotted path like my_robot.handler.MyHandler")

        module_name, class_name = target.rsplit(".", 1)
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        self.inner = cls(config)

    def setup(self) -> None:
        setup = getattr(self.inner, "setup", None)
        if callable(setup):
            setup()
        logger.info("custom hardware loaded: %s", self.options.get("class"))

    def on_command(self, command: str, value: Any = None) -> None:
        self.inner.on_command(command, value)

    def emergency_stop(self) -> None:
        self.inner.emergency_stop()

