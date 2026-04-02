"""Adafruit Motor HAT adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "motor_hat"
    description = "Adafruit Motor HAT adapter with optional accessory channels"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.module = optional_import("Adafruit_MotorHAT", "Adafruit_MotorHAT")
        self.mh = None
        self.drive_speed = self.option_int("drive_speed", 180)
        self.turn_speed = self.option_int("turn_speed", 180)
        self.drive_time = self.option_float("drive_time", 0.35)
        self.turn_time = self.option_float("turn_time", 0.2)
        self.left_motors = self.option_pins("left_motors") or [1, 2]
        self.right_motors = self.option_pins("right_motors") or [3, 4]
        self.up_motor = self.option_int("up_motor", 0)
        self.open_motor = self.option_int("open_motor", 0)

    def setup(self) -> None:
        if self.module is None:
            return
        address = self.options.get("address", 0x60)
        address = int(str(address), 16) if isinstance(address, str) else int(address)
        self.mh = self.module.Adafruit_MotorHAT(addr=address)

    def _motor(self, channel: int):
        if self.mh is None:
            return None
        if channel < 1 or channel > 4:
            return None
        return self.mh.getMotor(channel)

    def _apply(self, channels: list[int], direction: int, speed: int) -> None:
        for channel in channels:
            motor = self._motor(channel)
            if motor is None:
                continue
            motor.setSpeed(speed)
            motor.run(direction)

    def _release_all(self) -> None:
        if self.mh is None:
            return
        for channel in range(1, 5):
            motor = self.mh.getMotor(channel)
            motor.run(self.module.Adafruit_MotorHAT.RELEASE)

    def _pulse(self, left_direction: int, right_direction: int, speed: int, duration: float) -> None:
        if self.mh is None:
            return
        self._apply(self.left_motors, left_direction, speed)
        self._apply(self.right_motors, right_direction, speed)
        time.sleep(duration)
        self._release_all()

    def _pulse_single(self, channel: int, direction: int, speed: int, duration: float) -> None:
        motor = self._motor(channel)
        if motor is None:
            return
        motor.setSpeed(speed)
        motor.run(direction)
        time.sleep(duration)
        motor.run(self.module.Adafruit_MotorHAT.RELEASE)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.mh is None or self.module is None:
            self.log.info("command=%s value=%s", command, value)
            return

        mh = self.module.Adafruit_MotorHAT
        if self.matches(command, "forward"):
            self._pulse(mh.FORWARD, mh.FORWARD, self.drive_speed, self.drive_time)
        elif self.matches(command, "backward"):
            self._pulse(mh.BACKWARD, mh.BACKWARD, self.drive_speed, self.drive_time)
        elif self.matches(command, "left"):
            self._pulse(mh.BACKWARD, mh.FORWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "right"):
            self._pulse(mh.FORWARD, mh.BACKWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "lift_up") and self.up_motor:
            self._pulse_single(self.up_motor, mh.FORWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "lift_down") and self.up_motor:
            self._pulse_single(self.up_motor, mh.BACKWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "open") and self.open_motor:
            self._pulse_single(self.open_motor, mh.FORWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "close") and self.open_motor:
            self._pulse_single(self.open_motor, mh.BACKWARD, self.turn_speed, self.turn_time)
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        self._release_all()
