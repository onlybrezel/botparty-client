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
from botparty_robot.hardware import HARDWARE_DEPENDENCIES
from botparty_robot.hardware.base import BaseHardware
from botparty_robot.profile_options import HARDWARE_OPTION_MODELS

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "docs" / "generated" / "adapter-inventory.json"
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
                "commandValueSchemas": {
                    command: (
                        {
                            "oneOf": [
                                {"type": "null"},
                                {"type": "number", "minimum": -100, "maximum": 100},
                                {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": ["x", "y"],
                                    "properties": {
                                        "x": {"type": "number", "minimum": -1, "maximum": 1},
                                        "y": {"type": "number", "minimum": -1, "maximum": 1},
                                    },
                                },
                            ]
                        }
                        if command in adapter_type.motion_commands
                        else {"type": "null"}
                    )
                    for command in sorted(set(adapter_type.supported_commands))
                },
                "safeStop": adapter_type.safe_stop_capable,
                "close": adapter_type.close_capable,
                "supportLevel": adapter_type.support_level,
                "dependency": HARDWARE_DEPENDENCIES.get(adapter_type.profile_name),
                "optionsSchema": (
                    HARDWARE_OPTION_MODELS[adapter_type.profile_name].model_json_schema()
                    if adapter_type.profile_name in HARDWARE_OPTION_MODELS
                    else {"experimentalCustomOptions": True}
                ),
                "hilReports": sorted(
                    str(path.relative_to(ROOT))
                    for path in (ROOT / "reports" / "hil").glob("**/*.json")
                    if f'"adapterId": "{adapter_type.profile_name}"'
                    in path.read_text(encoding="utf-8", errors="replace")
                ),
            }
        )
    return sorted(entries, key=lambda entry: str(entry["adapterId"]))


def check_documentation(entries: list[dict[str, object]]) -> list[str]:
    documentation = (ROOT / "docs" / "adapter-support.md").read_text(encoding="utf-8")
    errors = []
    for page in sorted((ROOT / "docs" / "hardware").glob("*.md")):
        text = page.read_text(encoding="utf-8")
        if not any(
            marker in text for marker in ("Release status:", "Release scope:", "Current release:")
        ):
            errors.append(f"{page.relative_to(ROOT)} has no visible release status")
    release_profile = json.loads((ROOT / "release-profile.json").read_text(encoding="utf-8"))
    if release_profile.get("mode") == "media-only":
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        if "current release profile is **media-only**" not in readme:
            errors.append("README.md does not expose the media-only release status")
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
    expected = json.loads(json.dumps({"schemaVersion": 3, "adapters": entries}))
    try:
        generated = json.loads(GENERATED.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        generated = None
    if generated != expected:
        errors.append(
            "docs/generated/adapter-inventory.json is stale; run adapter-inventory.py --write-doc"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write-doc", action="store_true")
    args = parser.parse_args()
    entries = inventory()
    payload = {"schemaVersion": 3, "adapters": entries}
    if args.write_doc:
        GENERATED.parent.mkdir(parents=True, exist_ok=True)
        GENERATED.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return 0
    if args.check:
        errors = check_documentation(entries)
        if errors:
            sys.stderr.write("\n".join(errors) + "\n")
            return 1
        return 0
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
