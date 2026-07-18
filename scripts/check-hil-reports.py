#!/usr/bin/env python3
"""Validate versioned hardware-in-the-loop performance reports."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import math
import pkgutil
import platform
import re
import sys
from pathlib import Path

from botparty_robot import hardware
from botparty_robot.hardware.base import BaseHardware

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
INVENTORY_PATH = ROOT / "docs" / "generated" / "adapter-inventory.json"
PLACEHOLDER_MARKERS = ("REPLACE", "TODO", "EXAMPLE", "CHANGEME")


def _meaningful_string(value: object, *, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and not any(marker in value.upper() for marker in PLACEHOLDER_MARKERS)
    )


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _known_adapter_ids() -> set[str]:
    payload = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
    adapters = payload.get("adapters", []) if isinstance(payload, dict) else []
    return {
        str(adapter["adapterId"])
        for adapter in adapters
        if isinstance(adapter, dict) and isinstance(adapter.get("adapterId"), str)
    }


def template(device_class: str) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "clientVersion": "replace-with-release-version",
        "commit": "replace-with-40-character-commit-digest",
        "rawDataSha256": "replace-with-raw-data-sha256",
        "rawDataFile": "raw/replace-with-run-id.jsonl",
        "recordedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "runnerId": "replace-with-hardware-runner-id",
        "deviceSerialHash": "replace-with-pseudonymous-device-sha256",
        "firmwareVersion": "replace-with-firmware-version",
        "powerSupply": "replace-with-power-supply",
        "attestation": {
            "issuer": "https://token.actions.githubusercontent.com",
            "workflow": "hil.yml",
            "runId": "replace-with-ci-run-id",
            "reviewer": "replace-with-independent-reviewer",
        },
        "deviceClass": device_class,
        "adapterId": "replace-with-adapter-id",
        "hardware": {
            "model": platform.machine(),
            "os": platform.platform(),
            "kernel": platform.release(),
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
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 2:
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
        if not _finite_number(value) or value < 0:
            errors.append(f"{path}: {metric} must be a non-negative number")
        elif value > maximum:
            errors.append(f"{path}: {metric}={value} exceeds budget {maximum}")
    soak_hours = measurements.get("soakHours")
    if not _finite_number(soak_hours) or soak_hours < 24:
        errors.append(f"{path}: soakHours must be at least 24")
    slope = measurements.get("rssSlopeMiBPerHour")
    if not _finite_number(slope) or abs(slope) > 1:
        errors.append(f"{path}: rssSlopeMiBPerHour must be numeric and at most 1")
    for key in (
        "clientVersion",
        "adapterId",
        "hardware",
        "runnerId",
        "deviceSerialHash",
        "firmwareVersion",
        "powerSupply",
    ):
        if not payload.get(key):
            errors.append(f"{path}: {key} is required")
    for key in (
        "clientVersion",
        "adapterId",
        "runnerId",
        "firmwareVersion",
        "powerSupply",
    ):
        if not _meaningful_string(payload.get(key)):
            errors.append(f"{path}: {key} must not be a placeholder")
    hardware_metadata = payload.get("hardware")
    if not isinstance(hardware_metadata, dict):
        errors.append(f"{path}: hardware must be an object")
    else:
        for key in ("model", "os", "kernel", "python", "camera", "encoder"):
            if not _meaningful_string(hardware_metadata.get(key)):
                errors.append(f"{path}: hardware.{key} is required")
    adapter_id = payload.get("adapterId")
    if isinstance(adapter_id, str) and adapter_id not in _known_adapter_ids():
        errors.append(f"{path}: unknown adapterId {adapter_id!r}")
    serial_hash = payload.get("deviceSerialHash")
    if not isinstance(serial_hash, str) or re.fullmatch(r"[a-f0-9]{64}", serial_hash) is None:
        errors.append(f"{path}: deviceSerialHash must be a SHA-256 digest")
    raw_file = payload.get("rawDataFile")
    raw_digest = payload.get("rawDataSha256")
    if (
        not isinstance(raw_file, str)
        or Path(raw_file).is_absolute()
        or ".." in Path(raw_file).parts
    ):
        errors.append(f"{path}: rawDataFile must be a repository-relative path")
    else:
        raw_root = (ROOT / "reports" / "hil").resolve()
        raw_path = (raw_root / raw_file).resolve()
        if raw_path != raw_root and raw_root not in raw_path.parents:
            errors.append(f"{path}: rawDataFile escapes the HIL report root")
            raw_path = raw_root / "missing"
        if not raw_path.is_file():
            errors.append(f"{path}: raw data file is missing: {raw_file}")
        else:
            raw = raw_path.read_bytes()
            if not raw or len(raw) > 64 * 1024 * 1024:
                errors.append(f"{path}: raw data must be non-empty and at most 64 MiB")
            elif hashlib.sha256(raw).hexdigest() != raw_digest:
                errors.append(f"{path}: raw data digest does not match")
            else:
                try:
                    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
                except json.JSONDecodeError:
                    errors.append(f"{path}: raw data must be JSON Lines")
                else:
                    if not rows or any(not isinstance(row, dict) for row in rows):
                        errors.append(f"{path}: raw data requires object rows")
    recorded_at = payload.get("recordedAt")
    try:
        recorded = dt.datetime.fromisoformat(str(recorded_at).replace("Z", "+00:00"))
        if recorded.tzinfo is None:
            raise ValueError("timezone is required")
        age = dt.datetime.now(dt.timezone.utc) - recorded.astimezone(dt.timezone.utc)
        if age < dt.timedelta(0) or age > dt.timedelta(days=30):
            errors.append(f"{path}: HIL evidence must be no older than 30 days")
    except (TypeError, ValueError):
        errors.append(f"{path}: recordedAt must be an ISO-8601 timestamp")
    attestation = payload.get("attestation")
    if not isinstance(attestation, dict) or any(
        not _meaningful_string(attestation.get(key))
        for key in ("issuer", "workflow", "runId", "reviewer")
    ):
        errors.append(f"{path}: complete attestation metadata is required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", choices=sorted(BUDGETS))
    parser.add_argument("--require-adapter", action="append", default=[])
    parser.add_argument("--require-supported-moving", action="store_true")
    parser.add_argument("--client-version")
    parser.add_argument("--release-profile", type=Path)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    if args.template:
        json.dump(template(args.template), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    paths = args.paths or sorted((ROOT / "reports" / "hil").glob("**/*.json"))
    errors = [error for path in paths for error in validate(path)]
    valid_payloads: list[dict[str, object]] = []
    for path in paths:
        if validate(path):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            valid_payloads.append(payload)
    required_adapters = set(args.require_adapter)
    if args.release_profile is not None:
        try:
            profile = json.loads(args.release_profile.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid release profile: {exc}")
            profile = {}
        mode = profile.get("mode") if isinstance(profile, dict) else None
        moving = profile.get("movingAdapters") if isinstance(profile, dict) else None
        if (
            not isinstance(profile, dict)
            or profile.get("schemaVersion") != 1
            or mode not in {"media-only", "robot"}
            or not isinstance(moving, list)
        ):
            errors.append("release profile requires mode and movingAdapters")
        elif mode == "media-only" and moving:
            errors.append("media-only release profile must not declare moving adapters")
        elif mode == "robot" and not moving:
            errors.append("robot release profile must declare at least one moving adapter")
        elif any(not isinstance(item, str) or not item.strip() for item in moving):
            errors.append("release profile movingAdapters contains an invalid adapter id")
        else:
            required_adapters.update(str(item) for item in moving)
    if args.require_supported_moving:
        for module_info in pkgutil.iter_modules(hardware.__path__):
            if module_info.name.startswith("_") or module_info.name in {
                "base",
                "common",
                "gpio",
                "hardware_custom_example",
            }:
                continue
            module = importlib.import_module(f"botparty_robot.hardware.{module_info.name}")
            adapter_type: type[BaseHardware] = module.HardwareAdapter
            if adapter_type.support_level == "supported" and adapter_type.motion_commands:
                required_adapters.add(adapter_type.profile_name)
    for adapter_id in sorted(required_adapters):
        matches = [payload for payload in valid_payloads if payload.get("adapterId") == adapter_id]
        if args.client_version:
            matches = [
                payload
                for payload in matches
                if payload.get("clientVersion") == args.client_version
            ]
        if not matches:
            errors.append(
                f"missing current HIL report for adapter {adapter_id!r}"
                + (f" and version {args.client_version}" if args.client_version else "")
            )
    for payload in valid_payloads:
        commit = payload.get("commit")
        raw_digest = payload.get("rawDataSha256")
        if (
            not isinstance(commit, str)
            or re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit) is None
        ):
            errors.append(f"HIL report for {payload.get('adapterId')!r} lacks a commit digest")
        if not isinstance(raw_digest, str) or re.fullmatch(r"[a-f0-9]{64}", raw_digest) is None:
            errors.append(f"HIL report for {payload.get('adapterId')!r} lacks rawDataSha256")
    evidence_keys: set[tuple[object, ...]] = set()
    raw_digests: set[object] = set()
    for payload in valid_payloads:
        key = (
            payload.get("adapterId"),
            payload.get("runnerId"),
            payload.get("deviceSerialHash"),
            payload.get("recordedAt"),
        )
        if key in evidence_keys:
            errors.append(
                f"duplicate HIL evidence identity for adapter {payload.get('adapterId')!r}"
            )
        evidence_keys.add(key)
        raw_digest = payload.get("rawDataSha256")
        if raw_digest in raw_digests:
            errors.append(f"raw HIL data is reused by adapter {payload.get('adapterId')!r}")
        raw_digests.add(raw_digest)
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
