"""Robot command handlers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..config import ControlsConfig, SafetyConfig

logger = logging.getLogger("botparty.handler")


class BaseHandler(ABC):
    """Base class for robot command handlers. Override for your specific robot."""

    def __init__(self, controls: ControlsConfig, safety: SafetyConfig) -> None:
        self.controls = controls
        self.safety = safety

    @abstractmethod
    def on_command(self, command: str, value: object = None) -> None:
        """Handle an incoming control command."""
        ...

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediately stop all motors and actuators."""
        ...


class DefaultHandler(BaseHandler):
    """
    Default handler that logs commands.
    Replace with GPIO-based handler for actual robots.

    Supports: forward, backward, left, right, stop, camera_up, camera_down,
              action_1, action_2, action_3
    """

    def on_command(self, command: str, value: object = None) -> None:
        logger.info(f"🎮 Command: {command} = {value}")

        if self.controls.gpio_enabled:
            self._gpio_command(command, value)

    def emergency_stop(self) -> None:
        logger.warning("🛑 EMERGENCY STOP")
        if self.controls.gpio_enabled:
            self._stop_all_gpio()

    def _gpio_command(self, command: str, value: object = None) -> None:
        """Send GPIO commands. Requires RPi.GPIO or gpiozero."""
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-untyped]
        except ImportError:
            logger.debug("RPi.GPIO not available – skipping GPIO command")
            return

        pin_map = {
            "forward": self.controls.motor_left_forward,
            "backward": self.controls.motor_left_backward,
            "left": self.controls.motor_right_forward,
            "right": self.controls.motor_right_backward,
        }

        if command == "stop":
            self._stop_all_gpio()
        elif command in pin_map:
            pin = pin_map[command]
            if pin is not None:
                GPIO.setup(pin, GPIO.OUT)
                GPIO.output(pin, GPIO.HIGH)
                logger.debug(f"GPIO {pin} HIGH")

    def _stop_all_gpio(self) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore[import-untyped]
            for pin in [
                self.controls.motor_left_forward,
                self.controls.motor_left_backward,
                self.controls.motor_right_forward,
                self.controls.motor_right_backward,
            ]:
                if pin is not None:
                    GPIO.setup(pin, GPIO.OUT)
                    GPIO.output(pin, GPIO.LOW)
        except ImportError:
            pass
