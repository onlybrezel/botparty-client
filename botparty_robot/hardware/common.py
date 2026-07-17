"""Shared helpers for hardware adapters."""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger("botparty.hardware.common")

COMMAND_ALIASES: dict[str, set[str]] = {
    "forward": {"forward", "f"},
    "backward": {"backward", "b", "reverse"},
    "left": {"left", "l"},
    "right": {"right", "r"},
    "stop": {"stop", "s", "x"},
    "head_up": {"head_up", "camera_up", "q"},
    "head_down": {"head_down", "camera_down", "a"},
    "lift_up": {"lift_up", "up", "u", "w"},
    "lift_down": {"lift_down", "down", "d"},
    "open": {"open", "open_gripper", "o", "c"},
    "close": {"close", "close_gripper", "v"},
}

MOTION_COMMANDS = frozenset({"forward", "backward", "left", "right"})


def normalize_command(command: str) -> str:
    return command.strip().lower().replace("-", "_").replace(" ", "_")


def command_matches(command: str, *names: str) -> bool:
    normalized = normalize_command(command)
    for name in names:
        key = normalize_command(name)
        aliases = COMMAND_ALIASES.get(key, {key})
        if normalized in aliases or normalized == key:
            return True
    return False


def canonical_command(command: str) -> str:
    normalized = normalize_command(command)
    for canonical, aliases in COMMAND_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized


def optional_import(module_name: str, package_hint: str | None = None) -> Any:
    try:
        return importlib.import_module(module_name)
    except ImportError:
        message = f"Optional dependency '{module_name}' is not installed"
        if package_hint:
            message += f" (install package: {package_hint})"
        logger.warning(message)
        return None


def get_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_str(value: Any, default: str) -> str:
    return value if isinstance(value, str) and value else default


def get_pin_list(value: Any) -> list[int]:
    if isinstance(value, int):
        return [value]
    if isinstance(value, str):
        result: list[int] = []
        for part in value.split(","):
            try:
                result.append(int(part.strip()))
            except (TypeError, ValueError):
                continue
        return result
    if isinstance(value, Iterable):
        iterable_result: list[int] = []
        for part in value:
            try:
                iterable_result.append(int(part))
            except (TypeError, ValueError):
                continue
        return iterable_result
    return []
