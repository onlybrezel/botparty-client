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
        self.command_context: dict[str, Any] = {}
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

    def set_command_context(self, context: dict[str, Any] | None) -> None:
        self.command_context = dict(context or {})

    def value_float(self, value: Any, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        if isinstance(value, dict):
            for key in ("value", "v", "speed"):
                raw = value.get(key)
                if isinstance(raw, (int, float)):
                    return float(raw)
                if isinstance(raw, str):
                    try:
                        return float(raw.strip())
                    except ValueError:
                        continue
        return default

    def value_xy(
        self,
        value: Any,
        default: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, float]:
        if not isinstance(value, dict):
            return default

        x_raw = value.get("x")
        y_raw = value.get("y")
        if x_raw is None or y_raw is None:
            return default

        try:
            return float(x_raw), float(y_raw)
        except (TypeError, ValueError):
            return default

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
