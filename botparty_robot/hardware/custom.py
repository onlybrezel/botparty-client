"""Custom hardware adapter loader.

Looks for hardware_custom.py in the current working directory
and loads the HardwareAdapter class from it.

To use:
  1. Copy hardware_custom_example.py next to your config.yaml,
     rename it to hardware_custom.py, and fill in your motor code.
  2. Set hardware.type = "custom" in config.yaml.
  3. Run: python -m botparty_robot (from the same directory as config.yaml)
"""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from .base import BaseHardware

logger = logging.getLogger("botparty.hardware.custom")


def _load_custom_module():
    search_paths = [
        Path.cwd() / "hardware_custom.py",
        Path(__file__).parent.parent.parent / "hardware_custom.py",
    ]
    for path in search_paths:
        if path.exists():
            spec = importlib.util.spec_from_file_location("hardware_custom", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            logger.info("Loaded custom hardware from: %s", path)
            return module

    searched = "\n  ".join(str(p) for p in search_paths)
    raise FileNotFoundError(
        "hardware_custom.py not found. Searched:\n  " + searched + "\n\n"
        "Copy hardware_custom_example.py to hardware_custom.py "
        "in the same directory as your config.yaml and fill in your motor code."
    )


class HardwareAdapter(BaseHardware):
    profile_name = "custom"
    description = "Load HardwareAdapter from hardware_custom.py in the working directory"

    def __init__(self, config) -> None:
        super().__init__(config)
        module = _load_custom_module()
        if not hasattr(module, "HardwareAdapter"):
            raise AttributeError(
                "hardware_custom.py must define a class named HardwareAdapter. "
                "See hardware_custom_example.py for the expected structure."
            )
        self.inner = module.HardwareAdapter(config)

    def setup(self) -> None:
        if callable(getattr(self.inner, "setup", None)):
            self.inner.setup()

    def on_command(self, command: str, value: Any = None) -> None:
        self.inner.on_command(command, value)

    def emergency_stop(self) -> None:
        self.inner.emergency_stop()


    def on_command(self, command: str, value: Any = None) -> None:
        self.inner.on_command(command, value)

    def emergency_stop(self) -> None:
        self.inner.emergency_stop()

