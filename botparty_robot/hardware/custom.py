"""Custom hardware adapter loader.

Loads the HardwareAdapter class from hardware_custom.py next to the configured file.

To use:
  1. Copy hardware_custom_example.py next to your config.yaml,
     rename it to hardware_custom.py, and fill in your motor code.
  2. Set hardware.type = "custom" in config.yaml.
  3. Run: python -m botparty_robot --config /path/to/config.yaml
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import RobotConfig
from ..device_state import DeviceStateError, read_configuration_file
from .base import BaseHardware, HardwareCapabilities

logger = logging.getLogger("botparty.hardware.custom")


def _custom_search_paths(config_path: Path | None = None) -> list[Path]:
    configured = config_path or Path(os.environ.get("BOTPARTY_CONFIG", "config.yaml")).expanduser()
    return [
        configured.resolve().parent / "hardware_custom.py",
        Path(__file__).parent.parent.parent / "hardware_custom.py",
    ]


def _load_custom_module(config_path: Path | None = None) -> ModuleType:
    search_paths = list(dict.fromkeys(_custom_search_paths(config_path)))
    for path in search_paths:
        if path.exists():
            try:
                source = read_configuration_file(path)
            except DeviceStateError as exc:
                raise ImportError(f"Custom hardware file is not trusted: {path}") from exc
            name = "botparty_robot.hardware.hardware_custom"
            module = ModuleType(name)
            module.__file__ = str(path)
            module.__name__ = name
            module.__package__ = "botparty_robot.hardware"
            exec(compile(source, str(path), "exec"), module.__dict__)
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
    description = "Load HardwareAdapter from hardware_custom.py next to the config file"
    safe_stop_capable = False

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        module = _load_custom_module(config._source_path)
        if not hasattr(module, "HardwareAdapter"):
            raise AttributeError(
                "hardware_custom.py must define a class named HardwareAdapter. "
                "See hardware_custom_example.py for the expected structure."
            )
        self.inner = module.HardwareAdapter(config)

    def setup(self) -> None:
        if isinstance(self.inner, BaseHardware):
            self.inner.start()
            return
        setup = getattr(self.inner, "setup", None)
        if callable(setup):
            setup()

    def on_command(self, command: str, value: Any = None) -> None:
        permit = self.current_command_permit()
        if isinstance(self.inner, BaseHardware) and permit is not None:
            self.inner.execute(permit, command, value, self.command_context)
            return
        self.inner.on_command(command, value)

    def supports_command(self, command: str) -> bool:
        if isinstance(self.inner, BaseHardware):
            return self.inner.supports_command(command)
        supported = getattr(self.inner, "supported_commands", ())
        return command.strip().lower() in {str(value).strip().lower() for value in supported}

    def is_motion_command(self, command: str) -> bool:
        if isinstance(self.inner, BaseHardware):
            return self.inner.is_motion_command(command)
        moving = getattr(self.inner, "motion_commands", ())
        return command.strip().lower() in {str(value).strip().lower() for value in moving}

    def capabilities(self) -> HardwareCapabilities:
        if isinstance(self.inner, BaseHardware):
            capabilities = self.inner.capabilities()
            return type(capabilities)(
                commands=capabilities.commands,
                motion_commands=capabilities.motion_commands,
                safe_stop=bool(type(self.inner).__dict__.get("verified_safety_contract", False)),
                close=capabilities.close,
                support_level="experimental",
            )
        capabilities = super().capabilities()
        return type(capabilities)(
            commands=capabilities.commands,
            motion_commands=capabilities.motion_commands,
            safe_stop=False,
            close=callable(getattr(self.inner, "close", None)),
            support_level="experimental",
        )

    def emergency_stop(self) -> None:
        self.inner.emergency_stop()

    def reset_stop(self) -> None:
        reset = getattr(self.inner, "reset_stop", None)
        if callable(reset):
            reset()

    def _close_resources(self) -> None:
        close = getattr(self.inner, "close", None)
        if callable(close):
            close()
