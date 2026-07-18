#!/usr/bin/env python3
"""Enforce risk-weighted branch coverage regression floors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

FLOORS = {
    "botparty_robot/safety.py": 90.0,
    "botparty_robot/redaction.py": 90.0,
    "botparty_robot/auth.py": 90.0,
    "botparty_robot/config.py": 80.0,
    "botparty_robot/protocol.py": 90.0,
    "botparty_robot/gateway.py": 80.0,
    "botparty_robot/artifacts.py": 90.0,
    "botparty_robot/client_runtime.py": 75.0,
    "botparty_robot/client_commands.py": 80.0,
    "botparty_robot/ota.py": 90.0,
    "botparty_robot/camera.py": 75.0,
    "botparty_robot/publisher.py": 75.0,
    "botparty_robot/outbox.py": 85.0,
    "botparty_robot/command_queue.py": 90.0,
    "botparty_robot/remote_actions.py": 80.0,
}
TOTAL_FLOOR = 65.0
PROFILE_BEHAVIOR_FLOOR = 20.0

PROFILE_PREFIXES = (
    "botparty_robot/hardware/",
    "botparty_robot/tts/",
    "botparty_robot/video/",
)
PROFILE_SOURCE_TEMPLATES = {"botparty_robot/hardware/hardware_custom_example.py"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    args = parser.parse_args()
    report = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    files = report.get("files", {})
    failures: list[str] = []
    totals = report.get("totals", {})
    total_coverage = float(totals.get("percent_covered", 0.0))
    print(f"total: {total_coverage:.2f}% (floor {TOTAL_FLOOR:.2f}%)")
    if total_coverage + 1e-9 < TOTAL_FLOOR:
        failures.append(f"total: {total_coverage:.2f}% is below {TOTAL_FLOOR:.2f}%")
    for module, floor in FLOORS.items():
        summary = files.get(module, {}).get("summary")
        if not isinstance(summary, dict):
            failures.append(f"{module}: missing from coverage report")
            continue
        value = float(summary.get("percent_covered", 0.0))
        print(f"{module}: {value:.2f}% (floor {floor:.2f}%)")
        if value + 1e-9 < floor:
            failures.append(f"{module}: {value:.2f}% is below {floor:.2f}%")
    for module, details in sorted(files.items()):
        if not module.startswith(PROFILE_PREFIXES):
            continue
        if module in PROFILE_SOURCE_TEMPLATES:
            continue
        summary = details.get("summary")
        if not isinstance(summary, dict):
            failures.append(f"{module}: profile module is missing from coverage")
            continue
        value = float(summary.get("percent_covered", 0.0))
        if value + 1e-9 < PROFILE_BEHAVIOR_FLOOR:
            failures.append(
                f"{module}: {value:.2f}% is below profile behavior floor "
                f"{PROFILE_BEHAVIOR_FLOOR:.2f}%"
            )
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
