"""Pololu Maestro servo controller adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "maestro_servo"
    description = "Dual-servo drive adapter for Pololu Maestro"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.maestro = optional_import("maestro", "Maestro")
        self.controller = None
        self.left_channel = self.option_int("left_channel", 0)
        self.right_channel = self.option_int("right_channel", 1)
        self.center = self.option_int("center", 6000)
        self.forward = self.option_int("forward", 12000)
        self.backward = self.option_int("backward", 0)
        self.straight_delay = self.option_float("straight_delay", 0.35)
        self.turn_delay = self.option_float("turn_delay", 0.2)

    def setup(self) -> None:
        if self.maestro is None:
            return
        self.controller = self.maestro.Controller()
        self.controller.setAccel(self.left_channel, 4)
        self.controller.setAccel(self.right_channel, 4)
        self.controller.setTarget(self.left_channel, self.center)
        self.controller.setTarget(self.right_channel, self.center)

    def _set(self, left: int, right: int, duration: float) -> None:
        if self.controller is None:
            return
        self.controller.setTarget(self.left_channel, left)
        self.controller.setTarget(self.right_channel, right)
        time.sleep(duration)
        self.controller.setTarget(self.left_channel, self.center)
        self.controller.setTarget(self.right_channel, self.center)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.controller is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "forward"):
            self._set(self.forward, self.forward, self.straight_delay)
        elif self.matches(command, "backward"):
            self._set(self.backward, self.backward, self.straight_delay)
        elif self.matches(command, "left"):
            self._set(self.backward, self.forward, self.turn_delay)
        elif self.matches(command, "right"):
            self._set(self.forward, self.backward, self.turn_delay)
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.controller is not None:
            self.controller.setTarget(self.left_channel, self.center)
            self.controller.setTarget(self.right_channel, self.center)

