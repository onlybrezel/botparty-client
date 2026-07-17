#!/usr/bin/env python3
"""Emit adapter contract metadata and verify documentation coverage."""

from __future__ import annotations

import argparse
import importlib
import json
import pkgutil
import sys
from pathlib import Path

from botparty_robot import hardware
from botparty_robot.hardware.base import BaseHardware

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_MODULES = {"base", "common", "gpio", "hardware_custom_example"}


def inventory() -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for module_info in pkgutil.iter_modules(hardware.__path__):
        if module_info.name.startswith("_") or module_info.name in EXCLUDED_MODULES:
            continue
        module = importlib.import_module(f"botparty_robot.hardware.{module_info.name}")
        adapter_type = module.HardwareAdapter
        if not issubclass(adapter_type, BaseHardware):
            raise TypeError(f"{module_info.name}.HardwareAdapter does not implement BaseHardware")
        entries.append(
            {
                "module": module_info.name,
                "adapterId": adapter_type.profile_name,
                "description": adapter_type.description,
                "commands": sorted(set(adapter_type.supported_commands)),
                "motionCommands": sorted(set(adapter_type.motion_commands)),
                "safeStop": adapter_type.safe_stop_capable,
                "close": adapter_type.close_capable,
                "supportLevel": adapter_type.support_level,
            }
        )
    return sorted(entries, key=lambda entry: str(entry["adapterId"]))


def check_documentation(entries: list[dict[str, object]]) -> list[str]:
    documentation = (ROOT / "docs" / "adapter-support.md").read_text(encoding="utf-8")
    errors = []
    adapter_ids = [str(entry["adapterId"]) for entry in entries]
    duplicates = sorted(
        {adapter_id for adapter_id in adapter_ids if adapter_ids.count(adapter_id) > 1}
    )
    errors.extend(f"duplicate adapter ID {adapter_id}" for adapter_id in duplicates)
    for entry in entries:
        adapter_id = str(entry["adapterId"])
        level = str(entry["supportLevel"])
        if adapter_id not in documentation:
            errors.append(f"adapter {adapter_id} is missing from docs/adapter-support.md")
        if level not in {"supported", "community", "experimental", "deprecated"}:
            errors.append(f"adapter {adapter_id} has invalid support level {level}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    entries = inventory()
    if args.check:
        errors = check_documentation(entries)
        if errors:
            sys.stderr.write("\n".join(errors) + "\n")
            return 1
        return 0
    json.dump({"schemaVersion": 1, "adapters": entries}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
