"""GoPiGo3 adapter."""

from __future__ import annotations

from typing import Any

from ..config import RobotConfig
from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    supported_commands = ("forward", "backward", "left", "right", "stop")
    motion_commands = supported_commands[:-1]
    profile_name = "gopigo3"
    description = "Dexter Industries GoPiGo3 adapter"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.easygopigo3 = optional_import("easygopigo3", "easygopigo3")
        self.robot: Any = None
        self.drive_time = self.option_float("drive_time", 0.35)
        self.turn_time = self.option_float("turn_time", 0.15)

    def setup(self) -> None:
        if self.easygopigo3 is not None:
            self.robot = self.easygopigo3.EasyGoPiGo3()

    def on_command(self, command: str, value: Any = None) -> None:
        if self.robot is None:
            self.log.info("command=%s value=%s", command, value)
            return
        speed = self.robot.get_speed()
        if self.matches(command, "left"):

            def left() -> None:
                self.robot.set_motor_dps(self.robot.MOTOR_LEFT, -speed)
                self.robot.set_motor_dps(self.robot.MOTOR_RIGHT, speed)

            self.guarded_write(left)
            self.interruptible_sleep(self.turn_time)
        elif self.matches(command, "right"):

            def right() -> None:
                self.robot.set_motor_dps(self.robot.MOTOR_LEFT, speed)
                self.robot.set_motor_dps(self.robot.MOTOR_RIGHT, -speed)

            self.guarded_write(right)
            self.interruptible_sleep(self.turn_time)
        elif self.matches(command, "forward"):
            self.guarded_write(self.robot.forward)
            self.interruptible_sleep(self.drive_time)
        elif self.matches(command, "backward"):
            self.guarded_write(self.robot.backward)
            self.interruptible_sleep(self.drive_time)
        else:
            return
        self.robot.stop()

    def emergency_stop(self) -> None:
        if self.robot is not None:
            self.robot.stop()
