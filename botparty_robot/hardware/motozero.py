"""MotoZero adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .gpio import import_gpio


class HardwareAdapter(BaseHardware):
    profile_name = "motozero"
    description = "4-channel GPIO adapter for MotoZero"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.gpio = import_gpio()
        self.delay = self.option_float("motor_delay", 0.25)
        self.pins = {
            "motor1a": self.option_int("motor1a", 24),
            "motor1b": self.option_int("motor1b", 25),
            "motor1enable": self.option_int("motor1enable", 12),
            "motor2a": self.option_int("motor2a", 27),
            "motor2b": self.option_int("motor2b", 17),
            "motor2enable": self.option_int("motor2enable", 13),
            "motor3a": self.option_int("motor3a", 6),
            "motor3b": self.option_int("motor3b", 5),
            "motor3enable": self.option_int("motor3enable", 18),
            "motor4a": self.option_int("motor4a", 22),
            "motor4b": self.option_int("motor4b", 23),
            "motor4enable": self.option_int("motor4enable", 19),
        }

    def setup(self) -> None:
        if self.gpio is None:
            return
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)
        for pin in self.pins.values():
            self.gpio.setup(pin, self.gpio.OUT)
            self.gpio.output(pin, self.gpio.LOW)

    def _high(self, *keys: str) -> None:
        if self.gpio is None:
            return
        for key in keys:
            self.gpio.output(self.pins[key], self.gpio.HIGH)

    def _low_all(self) -> None:
        if self.gpio is None:
            return
        for pin in self.pins.values():
            self.gpio.output(pin, self.gpio.LOW)

    def _pulse(self, *keys: str) -> None:
        self._high(*keys)
        time.sleep(self.delay)
        self._low_all()

    def on_command(self, command: str, value: Any = None) -> None:
        if self.gpio is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if self.matches(command, "forward"):
            self._pulse("motor1b", "motor1enable", "motor2b", "motor2enable", "motor3a", "motor3enable", "motor4b", "motor4enable")
        elif self.matches(command, "backward"):
            self._pulse("motor1a", "motor1enable", "motor2a", "motor2enable", "motor3b", "motor3enable", "motor4a", "motor4enable")
        elif self.matches(command, "left"):
            self._pulse("motor3b", "motor3enable", "motor1a", "motor1enable", "motor2b", "motor2enable", "motor4b", "motor4enable")
        elif self.matches(command, "right"):
            self._pulse("motor3a", "motor3enable", "motor1b", "motor1enable", "motor2a", "motor2enable", "motor4a", "motor4enable")
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        self._low_all()

