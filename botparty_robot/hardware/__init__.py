"""BotParty hardware adapter registry."""

from __future__ import annotations

import importlib
import importlib.util
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

HARDWARE_DEPENDENCIES: Final[dict[str, tuple[tuple[str, ...], str]]] = {
    "adafruit_pwm": (("Adafruit_PCA9685",), "Adafruit-PCA9685"),
    "cozmo": (("cozmo",), "cozmo"),
    "gopigo2": (("gopigo",), "gopigo"),
    "gopigo3": (("easygopigo3",), "easygopigo3"),
    "l298n": (("RPi.GPIO",), "botparty-robot[gpio]"),
    "maestro_servo": (("maestro",), "Maestro"),
    "max7219": (("spidev",), "spidev"),
    "mc33926": (("dual_mc33926_rpi",), "dual-mc33926-rpi"),
    "mdd10": (("RPi.GPIO",), "botparty-robot[gpio]"),
    "megapi_board": (("megapi",), "megapi"),
    "motor_hat": (("Adafruit_MotorHAT",), "Adafruit-MotorHAT"),
    "motozero": (("RPi.GPIO",), "botparty-robot[gpio]"),
    "mqtt_pub": (("paho.mqtt.client",), "botparty-robot[mqtt]"),
    "navq": (("mavsdk",), "mavsdk"),
    "owi_arm": (("usb.core",), "botparty-robot[usb]"),
    "pololu": (("pololu_drv8835_rpi",), "pololu-drv8835-rpi"),
    "serial_board": (("serial",), "botparty-robot[serial]"),
    "telly": (("serial",), "botparty-robot[serial]"),
    "thunderborg": (("ThunderBorg", "ThunderBorg3"), "ThunderBorg"),
    "vector": (("anki_vector",), "anki-vector"),
}


def missing_hardware_dependency(profile: str) -> tuple[tuple[str, ...], str] | None:
    requirement = HARDWARE_DEPENDENCIES.get(profile)
    if requirement is None:
        return None
    modules, package = requirement
    for module in modules:
        try:
            if importlib.util.find_spec(module) is not None:
                return None
        except (ImportError, ModuleNotFoundError, ValueError):
            continue
    return modules, package


def normalize_profile_name(name: str) -> str:
    key = name.strip().lower().replace("/", "_")
    return PROFILE_ALIASES.get(key, key.replace("-", "_"))


def _auto_detect_profile(config: RobotConfig) -> str:
    explicit = str(config.hardware.options.get("auto_profile", "")).strip()
    if explicit:
        return normalize_profile_name(explicit)

    if any(
        Path(path).exists()
        for path in ("/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyACM0", "/dev/ttyACM1")
    ):
        return "serial_board"

    if Path("/dev/i2c-1").exists():
        return "adafruit_pwm"

    return "none"


def create_hardware(config: RobotConfig) -> BaseHardware:
    requested = normalize_profile_name(config.hardware.type)
    profile = _auto_detect_profile(config) if requested == "auto" else requested

    if requested == "auto":
        logger.info("Hardware auto-detection selected profile=%s", profile)

    missing = missing_hardware_dependency(profile)
    if missing is not None:
        modules, package = missing
        raise RuntimeError(
            f"hardware profile {profile!r} requires one of {', '.join(modules)}; install {package}"
        )

    module = importlib.import_module(f".{profile}", package=__name__)
    adapter: BaseHardware = module.HardwareAdapter(config)
    adapter.start()
    return adapter


__all__ = [
    "BaseHardware",
    "create_hardware",
    "missing_hardware_dependency",
    "normalize_profile_name",
]
