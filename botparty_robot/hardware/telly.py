"""Telly robot adapter."""

from __future__ import annotations

from typing import Any

from .serial_board import HardwareAdapter as SerialBoardAdapter


class HardwareAdapter(SerialBoardAdapter):
    profile_name = "telly"
    description = "Telly serial controller with Telly-friendly defaults"

    def __init__(self, config) -> None:
        super().__init__(config)
        if "device_name" not in self.options:
            self.device_name = "Telly"

    def on_command(self, command: str, value: Any = None) -> None:
        super().on_command(command, value)
