"""Compatibility helpers for reserved RemoTV-style hardware profile names."""

from __future__ import annotations

import logging
from typing import Any

from .base import LoggingHardware

logger = logging.getLogger("botparty.hardware.compat")


class ReservedCompatibilityHardware(LoggingHardware):
    """Adapter placeholder for profiles we want to preserve by name."""

    profile_name = "compat"
    description = "Reserved compatibility profile"

    def setup(self) -> None:
        logger.warning(
            "Hardware profile '%s' is registered as a compatibility placeholder. "
            "Add board-specific implementation in botparty_robot/hardware/%s.py when ready.",
            self.profile_name,
            self.profile_name,
        )

    def on_command(self, command: str, value: Any = None) -> None:
        logger.info(
            "compatibility profile '%s' received command=%s value=%s",
            self.profile_name,
            command,
            value,
        )

