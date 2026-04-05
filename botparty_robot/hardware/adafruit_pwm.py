"""Adafruit PCA9685 PWM adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "adafruit_pwm"
    description = "Adafruit PCA9685 steering and drive PWM adapter"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.module = optional_import("Adafruit_PCA9685", "Adafruit_PCA9685")
        self.pwm = None
        self.address = self.options.get("address", "0x40")
        self.pwm_freq = self.option_int("pwm_freq", 60)
        self.drive_channel = self.option_int("drive_channel", 0)
        self.steer_channel = self.option_int("steer_channel", 1)
        self.aux_channel = self.option_int("aux_channel", 2)
        self.neutral_drive = self.option_int("neutral_drive", 335)
        self.forward_drive = self.option_int("forward_drive", 445)
        self.forward_slow = self.option_int("forward_slow", 345)
        self.backward_drive = self.option_int("backward_drive", 270)
        self.backward_slow = self.option_int("backward_slow", 325)
        self.steer_left = self.option_int("steer_left", 300)
        self.steer_center = self.option_int("steer_center", 400)
        self.steer_right = self.option_int("steer_right", 500)
        self.aux_increment = self.option_int("aux_increment", 300)
        self.aux_decrement = self.option_int("aux_decrement", 400)
        self.aux_pos60 = self.option_int("aux_pos60", 490)
        self.aux_neg60 = self.option_int("aux_neg60", 100)

    def setup(self) -> None:
        if self.module is None:
            return
        address = int(str(self.address), 16) if isinstance(self.address, str) else int(self.address)
        self.pwm = self.module.PCA9685(address)
        self.pwm.set_pwm_freq(self.pwm_freq)
        self._set_pwm(self.drive_channel, self.neutral_drive)
        self._set_pwm(self.steer_channel, self.steer_center)

    def _set_pwm(self, channel: int, off: int) -> None:
        if self.pwm is not None:
            self.pwm.set_pwm(channel, 0, off)

    def _pulse_drive(self, steer_value: int | None, drive_value: int, duration: float, settle: int | None = None) -> None:
        if steer_value is not None:
            self._set_pwm(self.steer_channel, steer_value)
        self._set_pwm(self.drive_channel, drive_value)
        time.sleep(duration)
        if settle is not None:
            self._set_pwm(self.drive_channel, settle)
            time.sleep(0.4)
        self._set_pwm(self.steer_channel, self.steer_center)
        self._set_pwm(self.drive_channel, self.neutral_drive)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.pwm is None:
            self.log.info("command=%s value=%s", command, value)
            return

        if self.matches(command, "left"):
            self._pulse_drive(self.steer_left, self.forward_drive, 0.5)
        elif self.matches(command, "right"):
            self._pulse_drive(self.steer_right, self.forward_drive, 0.5)
        elif self.matches(command, "forward"):
            self._pulse_drive(None, self.forward_drive, 0.3, self.forward_slow)
        elif self.matches(command, "backward"):
            self._pulse_drive(None, self.backward_drive, 0.3, self.backward_slow)
        else:
            cmd = command.strip().upper()
            if cmd == "BL":
                self._pulse_drive(self.steer_left, self.backward_drive, 0.5)
            elif cmd == "BR":
                self._pulse_drive(self.steer_right, self.backward_drive, 0.5)
            elif cmd == "S2INC":
                self._set_pwm(self.aux_channel, self.aux_increment)
            elif cmd == "S2DEC":
                self._set_pwm(self.aux_channel, self.aux_decrement)
            elif cmd == "POS60":
                self._set_pwm(self.aux_channel, self.aux_pos60)
            elif cmd == "NEG60":
                self._set_pwm(self.aux_channel, self.aux_neg60)
            elif self.matches(command, "stop"):
                self.emergency_stop()

    def emergency_stop(self) -> None:
        self._set_pwm(self.steer_channel, self.steer_center)
        self._set_pwm(self.drive_channel, self.neutral_drive)

