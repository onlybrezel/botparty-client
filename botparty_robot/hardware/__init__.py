"""BotParty hardware adapter registry."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Final

from ..config import RobotConfig
from .base import BaseHardware

logger = logging.getLogger("botparty.hardware")

PROFILE_ALIASES: Final[dict[str, str]] = {
    "auto": "auto",
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


def _auto_detect_profile(config: RobotConfig) -> str:
    explicit = str(config.hardware.options.get("auto_profile", "")).strip()
    if explicit:
        return normalize_profile_name(explicit)

    if any(Path(path).exists() for path in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyACM1")):
        return "serial_board"

    if Path("/dev/i2c-1").exists():
        return "adafruit_pwm"

    return "none"


def create_hardware(config: RobotConfig) -> BaseHardware:
    requested = normalize_profile_name(config.hardware.type)
    profile = _auto_detect_profile(config) if requested == "auto" else requested

    if requested == "auto":
        logger.info("Hardware auto-detection selected profile=%s", profile)

    module = importlib.import_module(f".{profile}", package=__name__)
    adapter = module.HardwareAdapter(config)
    adapter.setup()
    return adapter


__all__ = ["BaseHardware", "create_hardware", "normalize_profile_name"]
