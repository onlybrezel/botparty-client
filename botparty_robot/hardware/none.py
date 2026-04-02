"""No-op hardware adapter."""

from .base import LoggingHardware


class HardwareAdapter(LoggingHardware):
    profile_name = "none"
    description = "No-op adapter that only logs commands"

