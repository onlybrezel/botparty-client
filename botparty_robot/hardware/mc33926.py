"""Dual MC33926 motor driver adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "mc33926"
    description = "Pololu dual MC33926 motor driver"

    def __init__(self, config) -> None:
        super().__init__(config)
        module = optional_import("dual_mc33926_rpi", "dual-mc33926-motor-driver-rpi")
        self.motors = getattr(module, "motors", None) if module else None
        self.drive_speed = self.option_int("driving_speed", 180)
        self.drive_time = self.option_float("drive_time", 0.3)

    def _run(self, left: int, right: int) -> None:
        if self.motors is None:
            return
        self.motors.enable()
        self.motors.setSpeeds(left, right)
        time.sleep(self.drive_time)
        self.motors.setSpeeds(0, 0)
        self.motors.disable()

    def on_command(self, command: str, value: Any = None) -> None:
        speed = self.drive_speed
        if self.motors is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "forward"):
            self._run(speed, -speed)
        elif self.matches(command, "backward"):
            self._run(-speed, speed)
        elif self.matches(command, "left"):
            self._run(-speed, -speed)
        elif self.matches(command, "right"):
            self._run(speed, speed)
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.motors is not None:
            self.motors.setSpeeds(0, 0)
            self.motors.disable()

