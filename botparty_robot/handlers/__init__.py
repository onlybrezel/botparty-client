"""Backward-compatible handler exports.

New BotParty robot integrations should live in ``botparty_robot.hardware``.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from ..config import ControlsConfig, RobotConfig, SafetyConfig
from ..hardware import create_hardware

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

    def __init__(self, controls: ControlsConfig, safety: SafetyConfig) -> None:
        super().__init__(controls, safety)
        self._adapter = create_hardware(
            RobotConfig(
                server={"claim_token": "legacy"},
                controls=controls,
                safety=safety,
                hardware={
                    "type": "l298n" if controls.gpio_enabled else "none",
                    "options": {
                        "forward_pins": [controls.motor_left_forward],
                        "backward_pins": [controls.motor_left_backward],
                        "left_pins": [controls.motor_right_forward],
                        "right_pins": [controls.motor_right_backward],
                    },
                },
            )
        )

    def on_command(self, command: str, value: Any = None) -> None:
        self._adapter.on_command(command, value)

    def emergency_stop(self) -> None:
        self._adapter.emergency_stop()
