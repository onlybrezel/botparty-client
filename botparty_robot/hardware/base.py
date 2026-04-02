"""Base classes for BotParty hardware adapters."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..config import RobotConfig
from .common import command_matches, get_float, get_int, get_pin_list, get_str

logger = logging.getLogger("botparty.hardware")


class BaseHardware(ABC):
    """Generic hardware adapter interface."""

    profile_name = "base"
    description = "Abstract BotParty hardware adapter"

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.controls = config.controls
        self.safety = config.safety
        self.options = config.hardware.options
        self.log = logging.getLogger(f"botparty.hardware.{self.profile_name}")

    def setup(self) -> None:
        """Optional one-time setup hook."""

    def matches(self, command: str, *names: str) -> bool:
        return command_matches(command, *names)

    def option_int(self, key: str, default: int) -> int:
        return get_int(self.options.get(key), default)

    def option_float(self, key: str, default: float) -> float:
        return get_float(self.options.get(key), default)

    def option_str(self, key: str, default: str) -> str:
        return get_str(self.options.get(key), default)

    def option_pins(self, key: str) -> list[int]:
        return get_pin_list(self.options.get(key))

    @abstractmethod
    def on_command(self, command: str, value: Any = None) -> None:
        """Handle a control command from the browser."""

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediately stop all actuators."""


class LoggingHardware(BaseHardware):
    """Safe fallback adapter that only logs commands."""

    profile_name = "none"
    description = "No-op adapter that only logs commands"

    def on_command(self, command: str, value: Any = None) -> None:
        self.log.info("command=%s value=%s", command, value)

    def emergency_stop(self) -> None:
        self.log.warning("emergency_stop")
