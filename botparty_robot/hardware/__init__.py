"""BotParty hardware adapter registry."""

from __future__ import annotations

import importlib
from typing import Final

from ..config import RobotConfig
from .base import BaseHardware

PROFILE_ALIASES: Final[dict[str, str]] = {
    "none": "none",
    "custom": "custom",
    "hardware_custom": "custom",
    "hardware_custom_example": "custom",
    "serial-board": "serial_board",
    "serial_board": "serial_board",
    "mqtt-pub": "mqtt_pub",
    "mqtt_pub": "mqtt_pub",
    "maestro-servo": "maestro_servo",
    "maestro_servo": "maestro_servo",
    "motor-hat": "motor_hat",
    "motor_hat": "motor_hat",
}


def normalize_profile_name(name: str) -> str:
    key = name.strip().lower().replace("/", "_")
    return PROFILE_ALIASES.get(key, key.replace("-", "_"))


def create_hardware(config: RobotConfig) -> BaseHardware:
    profile = normalize_profile_name(config.hardware.type)
    module = importlib.import_module(f".{profile}", package=__name__)
    adapter = module.HardwareAdapter(config)
    adapter.setup()
    return adapter


__all__ = ["BaseHardware", "create_hardware", "normalize_profile_name"]
