"""ThunderBorg adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "thunderborg"
    description = "PiBorg ThunderBorg motor driver"

    def __init__(self, config) -> None:
        super().__init__(config)
        module = optional_import("ThunderBorg3", "thunderborg")
        if module is None:
            module = optional_import("ThunderBorg", "thunderborg")
        self.module = module
        self.board = None
        self.left_motor_max = self.option_float("left_motor_max", 1.0)
        self.right_motor_max = self.option_float("right_motor_max", 1.0)
        self.sleep_time = self.option_float("sleep_time", 0.3)
        self.address = self.options.get("address")

    def setup(self) -> None:
        if self.module is None:
            return
        self.board = self.module.ThunderBorg()
        if self.address is not None:
            self.board.i2cAddress = int(str(self.address), 16) if isinstance(self.address, str) else int(self.address)
        self.board.Init()
        if not getattr(self.board, "foundChip", True):
            self.log.warning("No ThunderBorg board detected")
            self.board = None

    def _run(self, left: float, right: float) -> None:
        if self.board is None:
            return
        self.board.SetMotor1(left)
        self.board.SetMotor2(right)
        time.sleep(self.sleep_time)
        self.board.SetMotors(0.0)

    def on_command(self, command: str, value: Any = None) -> None:
        inverse_right = self.right_motor_max * -1
        inverse_left = self.left_motor_max * -1
        if self.board is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "forward"):
            self._run(inverse_left, self.right_motor_max)
        elif self.matches(command, "backward"):
            self._run(self.left_motor_max, inverse_right)
        elif self.matches(command, "left"):
            self._run(self.left_motor_max, self.right_motor_max)
        elif self.matches(command, "right"):
            self._run(inverse_left, inverse_right)
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.board is not None:
            self.board.SetMotors(0.0)
