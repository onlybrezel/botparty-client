"""MegaPi board adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "megapi_board"
    description = "Makeblock MegaPi-based tracked robot adapter"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.megapi = optional_import("megapi", "megapi")
        self.bot = None
        self.motor_time = self.option_float("motor_time", 0.2)
        self.driving_speed = self.option_int("driving_speed", 150)
        self.arm_speed = self.option_int("arm_speed", 50)
        self.grabber_speed = self.option_int("grabber_speed", 50)
        self.left_track = self.option_int("left_track_port", 2)
        self.right_track = self.option_int("right_track_port", 3)
        self.grabber = self.option_int("grabber_port", 4)
        self.arm = self.option_int("arm_port", 1)

    def setup(self) -> None:
        if self.megapi is None:
            return
        self.bot = self.megapi.MegaPi()
        self.bot.start()

    def _motor_run(self, port: int, speed: int) -> None:
        if self.bot is None:
            return
        if hasattr(self.bot, "motorRun"):
            self.bot.motorRun(port, speed)
        elif hasattr(self.bot, "encoderMotorRun"):
            self.bot.encoderMotorRun(port, speed)

    def _pulse(self, commands: list[tuple[int, int]], duration: float | None = None) -> None:
        if self.bot is None:
            return
        for port, speed in commands:
            self._motor_run(port, speed)
        time.sleep(duration or self.motor_time)
        for port, _ in commands:
            self._motor_run(port, 0)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.bot is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "forward"):
            self._pulse([(self.left_track, -self.driving_speed), (self.right_track, self.driving_speed)])
        elif self.matches(command, "backward"):
            self._pulse([(self.left_track, self.driving_speed), (self.right_track, -self.driving_speed)])
        elif self.matches(command, "left"):
            self._pulse([(self.left_track, self.driving_speed), (self.right_track, self.driving_speed)])
        elif self.matches(command, "right"):
            self._pulse([(self.left_track, -self.driving_speed), (self.right_track, -self.driving_speed)])
        elif self.matches(command, "lift_up"):
            self._pulse([(self.arm, self.arm_speed)])
        elif self.matches(command, "lift_down"):
            self._pulse([(self.arm, -self.arm_speed)])
        elif self.matches(command, "open"):
            self._pulse([(self.grabber, self.grabber_speed)])
        elif self.matches(command, "close"):
            self._pulse([(self.grabber, -self.grabber_speed)])
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.bot is None:
            return
        for port in (self.left_track, self.right_track, self.arm, self.grabber):
            self._motor_run(port, 0)
