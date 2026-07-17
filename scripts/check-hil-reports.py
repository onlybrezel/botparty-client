#!/usr/bin/env python3
"""Validate versioned hardware-in-the-loop performance reports."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

BUDGETS = {
    "low": {
        "controlReadyP95Ms": 20_000,
        "commandP99Ms": 250,
        "emergencyStopP99Ms": 150,
        "rssSteadyMiB": 350,
        "shutdownP99Ms": 8_000,
    },
    "medium": {
        "controlReadyP95Ms": 12_000,
        "commandP99Ms": 150,
        "emergencyStopP99Ms": 100,
        "rssSteadyMiB": 450,
        "shutdownP99Ms": 6_000,
    },
    "reference": {
        "controlReadyP95Ms": 8_000,
        "commandP99Ms": 100,
        "emergencyStopP99Ms": 75,
        "rssSteadyMiB": 550,
        "shutdownP99Ms": 5_000,
    },
}
ROOT = Path(__file__).resolve().parents[1]


def template(device_class: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "clientVersion": "replace-with-release-version",
        "deviceClass": device_class,
        "adapterId": "replace-with-adapter-id",
        "hardware": {
            "model": platform.machine(),
            "os": platform.platform(),
            "python": platform.python_version(),
            "camera": "none",
            "encoder": "none",
        },
        "measurements": {
            "controlReadyP95Ms": None,
            "commandP99Ms": None,
            "emergencyStopP99Ms": None,
            "rssSteadyMiB": None,
            "shutdownP99Ms": None,
            "soakHours": 24,
            "rssSlopeMiBPerHour": None,
        },
    }


def validate(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: unreadable JSON ({exc})"]
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        return [f"{path}: unsupported schemaVersion"]
    device_class = payload.get("deviceClass")
    if device_class not in BUDGETS:
        return [f"{path}: unknown deviceClass {device_class!r}"]
    measurements = payload.get("measurements")
    if not isinstance(measurements, dict):
        return [f"{path}: measurements must be an object"]
    errors: list[str] = []
    for metric, maximum in BUDGETS[device_class].items():
        value = measurements.get(metric)
        if not isinstance(value, (int, float)) or value < 0:
            errors.append(f"{path}: {metric} must be a non-negative number")
        elif value > maximum:
            errors.append(f"{path}: {metric}={value} exceeds budget {maximum}")
    soak_hours = measurements.get("soakHours")
    if not isinstance(soak_hours, (int, float)) or soak_hours < 24:
        errors.append(f"{path}: soakHours must be at least 24")
    slope = measurements.get("rssSlopeMiBPerHour")
    if not isinstance(slope, (int, float)) or slope > 1:
        errors.append(f"{path}: rssSlopeMiBPerHour must be numeric and at most 1")
    for key in ("clientVersion", "adapterId", "hardware"):
        if not payload.get(key):
            errors.append(f"{path}: {key} is required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", choices=sorted(BUDGETS))
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    if args.template:
        json.dump(template(args.template), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    paths = args.paths or sorted((ROOT / "reports" / "hil").glob("**/*.json"))
    errors = [error for path in paths for error in validate(path)]
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
