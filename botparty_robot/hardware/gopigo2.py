"""GoPiGo2 adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "gopigo2"
    description = "Dexter Industries GoPiGo2 adapter"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.gopigo = optional_import("gopigo", "gopigo")
        self.drive_time = self.option_float("drive_time", 0.35)
        self.turn_time = self.option_float("turn_time", 0.15)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.gopigo is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "left"):
            self.gopigo.left_rot()
            time.sleep(self.turn_time)
        elif self.matches(command, "right"):
            self.gopigo.right_rot()
            time.sleep(self.turn_time)
        elif self.matches(command, "forward"):
            self.gopigo.forward()
            time.sleep(self.drive_time)
        elif self.matches(command, "backward"):
            self.gopigo.backward()
            time.sleep(self.drive_time)
        else:
            return
        self.gopigo.stop()

    def emergency_stop(self) -> None:
        if self.gopigo is not None:
            self.gopigo.stop()

