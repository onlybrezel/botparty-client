"""Example custom hardware adapter."""

from __future__ import annotations

from typing import Any

from .base import BaseHardware


class ExampleCustomHardware(BaseHardware):
    profile_name = "hardware_custom_example"
    description = "Example for user-defined custom adapters"

    def setup(self) -> None:
        print("ExampleCustomHardware setup()")

    def on_command(self, command: str, value: Any = None) -> None:
        print(f"ExampleCustomHardware command={command} value={value}")

    def emergency_stop(self) -> None:
        print("ExampleCustomHardware emergency_stop()")

