"""Base classes for BotParty hardware adapters."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from ..config import RobotConfig
from ..safety import CommandPermit
from .common import (
    MOTION_COMMANDS,
    canonical_command,
    command_matches,
    get_float,
    get_int,
    get_pin_list,
    get_str,
)

logger = logging.getLogger("botparty.hardware")
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class HardwareCapabilities:
    commands: tuple[str, ...]
    motion_commands: tuple[str, ...]
    safe_stop: bool
    close: bool
    support_level: str


class BaseHardware(ABC):
    """Generic hardware adapter interface."""

    profile_name = "base"
    description = "Abstract BotParty hardware adapter"
    supported_commands = tuple(sorted(MOTION_COMMANDS | {"stop"}))
    motion_commands = tuple(sorted(MOTION_COMMANDS))
    support_level = "community"
    safe_stop_capable = True
    close_capable = True

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.safety = config.safety
        self.options = config.hardware.options
        self.command_context: dict[str, Any] = {}
        self.log = logging.getLogger(f"botparty.hardware.{self.profile_name}")
        self._worker_context = threading.local()
        self._close_lock = threading.Lock()
        self._closed = False

    def setup(self) -> None:
        """Optional one-time setup hook."""
        return None

    def start(self) -> None:
        """Start the adapter once; retained setup hooks run behind this lifecycle boundary."""
        self.setup()

    def matches(self, command: str, *names: str) -> bool:
        return command_matches(command, *names)

    def option_int(self, key: str, default: int) -> int:
        return get_int(self.options.get(key), default)

    def option_float(self, key: str, default: float) -> float:
        return get_float(self.options.get(key), default)

    def option_str(self, key: str, default: str) -> str:
        return get_str(self.options.get(key), default)

    def option_pins(self, key: str) -> list[int]:
        return get_pin_list(self.options.get(key))

    def set_command_context(self, context: dict[str, Any] | None) -> None:
        self.command_context = dict(context or {})

    def execute(
        self,
        permit: CommandPermit,
        command: str,
        value: Any = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Run a command under a permit that a concurrent stop can invalidate."""

        permit.ensure_active()
        self._worker_context.permit = permit
        try:
            self.set_command_context(context)
            self.on_command(command, value)
            permit.ensure_active()
        finally:
            self._worker_context.permit = None

    def ensure_command_active(self) -> None:
        permit = getattr(self._worker_context, "permit", None)
        if permit is not None:
            permit.ensure_active()

    def current_command_permit(self) -> CommandPermit | None:
        return getattr(self._worker_context, "permit", None)

    def interruptible_sleep(self, seconds: float) -> None:
        permit = getattr(self._worker_context, "permit", None)
        if permit is None:
            time.sleep(max(0.0, seconds))
            return
        permit.wait(seconds)

    def guarded_write(self, operation: Callable[[], _T]) -> _T:
        """Perform one active actuator write without racing the safety latch."""

        permit = getattr(self._worker_context, "permit", None)
        if permit is None:
            return operation()
        return cast(_T, permit.run_guarded(operation))

    def is_motion_command(self, command: str) -> bool:
        configured = self.options.get("motion_commands")
        extra = (
            {canonical_command(str(value)) for value in configured}
            if isinstance(configured, list)
            else set()
        )
        return canonical_command(command) in set(self.motion_commands) | extra

    def capabilities(self) -> HardwareCapabilities:
        return HardwareCapabilities(
            commands=tuple(sorted(set(self.supported_commands))),
            motion_commands=tuple(sorted(set(self.motion_commands))),
            safe_stop=self.safe_stop_capable,
            close=self.close_capable,
            support_level=self.support_level,
        )

    def reset_stop(self) -> None:
        """Optional adapter hook after the controller authorizes a latch reset."""
        return None

    def close(self) -> None:
        """Release adapter resources after a confirmed stop."""
        with self._close_lock:
            if self._closed:
                return
            self.apply_emergency_stop()
            try:
                self._close_resources()
            finally:
                self._closed = True

    def _close_resources(self) -> None:
        """Adapter-specific resource cleanup hook."""
        return None

    def apply_emergency_stop(self) -> None:
        """Exception-safe entry point used by the client safety controller."""

        try:
            self.emergency_stop()
        except Exception as exc:
            self.log.error("emergency stop failed: %s", exc)

    def value_float(self, value: Any, default: float = 0.0) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return default
        if isinstance(value, dict):
            for key in ("value", "v", "speed"):
                raw = value.get(key)
                if isinstance(raw, (int, float)):
                    return float(raw)
                if isinstance(raw, str):
                    try:
                        return float(raw.strip())
                    except ValueError:
                        continue
        return default

    def value_xy(
        self,
        value: Any,
        default: tuple[float, float] = (0.0, 0.0),
    ) -> tuple[float, float]:
        if not isinstance(value, dict):
            return default

        x_raw = value.get("x")
        y_raw = value.get("y")
        if x_raw is None or y_raw is None:
            return default

        try:
            return float(x_raw), float(y_raw)
        except (TypeError, ValueError):
            return default

    @abstractmethod
    def on_command(self, command: str, value: Any = None) -> None:
        """Handle a control command from the browser."""

    @abstractmethod
    def emergency_stop(self) -> None:
        """Immediately stop all actuators."""


class LoggingHardware(BaseHardware):
    """Safe fallback adapter that only logs commands."""

    profile_name = "none"
    description = "No-op adapter that only logs commands"
    supported_commands: tuple[str, ...] = ()
    motion_commands: tuple[str, ...] = ()
    support_level = "supported"

    def on_command(self, command: str, value: Any = None) -> None:
        self.log.info("command=%s value=%s", command, value)

    def emergency_stop(self) -> None:
        self.log.warning("emergency_stop")
