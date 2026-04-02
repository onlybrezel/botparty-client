"""OWI USB robotic arm adapter."""

from __future__ import annotations

import time
from typing import Any

from .base import BaseHardware
from .common import optional_import


class HardwareAdapter(BaseHardware):
    profile_name = "owi_arm"
    description = "OWI USB robotic arm controller"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.usb = optional_import("usb.core", "pyusb")
        self.arm = None
        self.led = 0
        self.step_seconds = self.option_float("step_seconds", 0.15)
        self.vendor_id = self.option_int("vendor_id", 0x1267)
        self.product_id = self.option_int("product_id", 0x0000)

    def setup(self) -> None:
        if self.usb is None:
            return
        self.arm = self.usb.find(idVendor=self.vendor_id, idProduct=self.product_id)
        if self.arm is None:
            self.log.warning("OWI arm not found on USB")

    def _send(self, payload: list[int]) -> None:
        if self.arm is None:
            return
        payload[2] = self.led
        self.arm.ctrl_transfer(0x40, 6, 0x100, 0, payload, 3)

    def _move(self, payload: list[int]) -> None:
        self._send(payload)
        time.sleep(self.step_seconds)
        self._send([0, 0, self.led])

    def on_command(self, command: str, value: Any = None) -> None:
        if self.arm is None:
            self.log.info("command=%s value=%s", command, value)
            return
        mapping = {
            "left": [0, 2, 0],
            "right": [0, 1, 0],
            "backward": [128, 0, 0],
            "forward": [64, 0, 0],
            "lift_up": [16, 0, 0],
            "lift_down": [32, 0, 0],
            "head_up": [4, 0, 0],
            "head_down": [8, 0, 0],
            "open": [2, 0, 0],
            "close": [1, 0, 0],
        }
        for canonical, payload in mapping.items():
            if self.matches(command, canonical):
                self._move(payload.copy())
                return
        if command == "1":
            self.led = 1
            self._move([0, 0, 1])
        elif command == "0":
            self.led = 0
            self._move([0, 0, 0])
        elif self.matches(command, "stop"):
            self.emergency_stop()

    def emergency_stop(self) -> None:
        self._send([0, 0, self.led])

