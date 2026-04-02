"""MAX7219 LED matrix adapter."""

from __future__ import annotations

from typing import Any

from .base import BaseHardware
from .common import optional_import

LED_PATTERNS = {
    "LED_OFF": [0x0] * 8,
    "LED_FULL": [0xFF] * 8,
    "LED_E_SMILEY": [0x0, 0x0, 0x24, 0x0, 0x42, 0x3C, 0x0, 0x0],
    "LED_E_SAD": [0x0, 0x0, 0x24, 0x0, 0x0, 0x3C, 0x42, 0x0],
    "LED_E_TONGUE": [0x0, 0x0, 0x24, 0x0, 0x42, 0x3C, 0xC, 0x0],
    "LED_E_SURPRISED": [0x0, 0x0, 0x24, 0x0, 0x18, 0x24, 0x24, 0x18],
}


class HardwareAdapter(BaseHardware):
    profile_name = "max7219"
    description = "SPI LED matrix expressions and brightness"

    def __init__(self, config) -> None:
        super().__init__(config)
        self.spidev = optional_import("spidev", "spidev")
        self.spi = None
        self.columns = [1, 2, 3, 4, 5, 6, 7, 8]
        self.rotate = self.option_int("rotate", 0)

    def setup(self) -> None:
        if self.spidev is None:
            return
        self.spi = self.spidev.SpiDev()
        bus = self.option_int("bus", 0)
        device = self.option_int("device", 0)
        self.spi.open(bus, device)
        self.spi.max_speed_hz = self.option_int("max_speed_hz", 1_000_000)
        for register, value in ((0x09, 0x00), (0x0A, 0x03), (0x0B, 0x07), (0x0C, 0x01), (0x0F, 0x00)):
            self.spi.writebytes([register])
            self.spi.writebytes([value])
        self._draw("LED_OFF")

    def _set_intensity(self, value: int) -> None:
        if self.spi is None:
            return
        self.spi.writebytes([0x0A])
        self.spi.writebytes([value])

    def _draw(self, name: str) -> None:
        if self.spi is None:
            return
        pattern = list(LED_PATTERNS.get(name, LED_PATTERNS["LED_OFF"]))
        if self.rotate == 180:
            pattern.reverse()
        for index, column in enumerate(self.columns):
            self.spi.xfer([column, pattern[index]])

    def on_command(self, command: str, value: Any = None) -> None:
        if self.spi is None:
            self.log.info("command=%s value=%s", command, value)
            return
        if command == "LED_LOW":
            self._draw("LED_FULL")
            self._set_intensity(0x00)
        elif command == "LED_MED":
            self._draw("LED_FULL")
            self._set_intensity(0x06)
        elif command == "LED_FULL":
            self._draw("LED_FULL")
            self._set_intensity(0x0F)
        else:
            self._draw(command)

    def emergency_stop(self) -> None:
        self._draw("LED_OFF")

