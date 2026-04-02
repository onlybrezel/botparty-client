"""Simple GPIO L298N-style motor driver adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .gpio import import_gpio, set_low, setup_output_pins


class HardwareAdapter(BaseHardware):
    profile_name = "l298n"
    description = "GPIO pulse adapter for L298N-style direction pins"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.gpio = import_gpio()
        self.forward_pins = tuple(self.option_pins("forward_pins"))
        self.backward_pins = tuple(self.option_pins("backward_pins"))
        self.left_pins = tuple(self.option_pins("left_pins"))
        self.right_pins = tuple(self.option_pins("right_pins"))
        self.drive_seconds = self.option_float("drive_seconds", 0.35)
        self.turn_seconds = self.option_float("turn_seconds", 0.2)

    def setup(self) -> None:
        if self.gpio is None:
            return
        pins = [
            *self.forward_pins,
            *self.backward_pins,
            *self.left_pins,
            *self.right_pins,
        ]
        if pins:
            setup_output_pins(self.gpio, pins)

    def on_command(self, command: str, value: Any = None) -> None:
        if self.gpio is None:
            self.log.info("command=%s value=%s", command, value)
            return

        if self.matches(command, "stop"):
            self.emergency_stop()
            return

        pins: tuple[int, ...] = ()
        duration = 0.0
        if self.matches(command, "forward"):
            pins, duration = self.forward_pins, self.drive_seconds
        elif self.matches(command, "backward"):
            pins, duration = self.backward_pins, self.drive_seconds
        elif self.matches(command, "left"):
            pins, duration = self.left_pins, self.turn_seconds
        elif self.matches(command, "right"):
            pins, duration = self.right_pins, self.turn_seconds
        if not pins:
            self.log.debug("ignoring unsupported command=%s", command)
            return

        for pin in pins:
            self.gpio.output(pin, self.gpio.HIGH)
        time.sleep(duration)
        set_low(self.gpio, pins)

    def emergency_stop(self) -> None:
        if self.gpio is None:
            self.log.warning("emergency_stop without GPIO backend")
            return
        set_low(
            self.gpio,
            [
                *self.forward_pins,
                *self.backward_pins,
                *self.left_pins,
                *self.right_pins,
            ],
        )
