"""Shared GPIO helpers."""

from __future__ import annotations

import contextlib
import logging
from typing import Iterable

logger = logging.getLogger("botparty.hardware.gpio")


def import_gpio():
    try:
        import RPi.GPIO as gpio  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("RPi.GPIO is not installed; GPIO adapter will log only")
        return None
    return gpio


def setup_output_pins(gpio, pins: Iterable[int]) -> None:
    gpio.setwarnings(False)
    gpio.setmode(gpio.BCM)
    for pin in pins:
        gpio.setup(pin, gpio.OUT)
        gpio.output(pin, gpio.LOW)


def set_low(gpio, pins: Iterable[int]) -> None:
    for pin in pins:
        with contextlib.suppress(Exception):
            gpio.output(pin, gpio.LOW)

