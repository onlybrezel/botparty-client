"""Cytron MDD10 adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .gpio import import_gpio


class HardwareAdapter(BaseHardware):
    profile_name = "mdd10"
    description = "Cytron MDD10 GPIO + PWM motor controller"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.gpio = import_gpio()
        self.an1 = self.option_int("an1", 12)
        self.an2 = self.option_int("an2", 13)
        self.dig1 = self.option_int("dig1", 26)
        self.dig2 = self.option_int("dig2", 24)
        self.turn_delay = self.option_float("turn_delay", 0.2)
        self.straight_delay = self.option_float("straight_delay", 0.35)
        self.speed_percent = self.option_int("speed_percent", 60)
        self.max_speed_percent = self.option_int("max_speed_percent", 100)
        self.max_speed_enabled = False
        self.p1 = None
        self.p2 = None

    def setup(self) -> None:
        if self.gpio is None:
            return
        self.gpio.setmode(self.gpio.BCM)
        self.gpio.setwarnings(False)
        for pin in (self.an1, self.an2, self.dig1, self.dig2):
            self.gpio.setup(pin, self.gpio.OUT)
        self.p1 = self.gpio.PWM(self.an1, 100)
        self.p2 = self.gpio.PWM(self.an2, 100)
        self.p1.start(0)
        self.p2.start(0)

    def _move(self, dig1: bool, dig2: bool, delay: float) -> None:
        if self.gpio is None or self.p1 is None or self.p2 is None:
            return
        speed = self.max_speed_percent if self.max_speed_enabled else self.speed_percent
        self.gpio.output(self.dig1, self.gpio.HIGH if dig1 else self.gpio.LOW)
        self.gpio.output(self.dig2, self.gpio.HIGH if dig2 else self.gpio.LOW)
        self.p1.ChangeDutyCycle(speed)
        self.p2.ChangeDutyCycle(speed)
        time.sleep(delay)
        self.p1.ChangeDutyCycle(0)
        self.p2.ChangeDutyCycle(0)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.gpio is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if command.strip().upper() == "MAXSPEED":
            self.max_speed_enabled = True
            return
        if self.matches(command, "forward"):
            self._move(False, False, self.straight_delay)
        elif self.matches(command, "backward"):
            self._move(True, True, self.straight_delay)
        elif self.matches(command, "left"):
            self._move(False, True, self.turn_delay)
        elif self.matches(command, "right"):
            self._move(True, False, self.turn_delay)
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        if self.p1 is not None and self.p2 is not None:
            self.p1.ChangeDutyCycle(0)
            self.p2.ChangeDutyCycle(0)

