#!/usr/bin/env python3
"""Validate externally attested canary and rollback evidence before promotion."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"invalid canary report: {exc}\n")
        return 1
    errors: list[str] = []
    if not isinstance(report, dict) or report.get("schemaVersion") != 1:
        errors.append("unsupported canary report schema")
        report = {}
    if report.get("releaseTag") != args.tag:
        errors.append("canary release tag does not match promotion tag")
    commit = str(report.get("commit", "")).lower()
    expected_commit = args.commit.lower()
    if re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", expected_commit) is None:
        errors.append("promotion commit must be immutable")
    if re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", commit) is None:
        errors.append("canary report requires an immutable commit")
    elif commit != expected_commit:
        errors.append("canary report commit does not match the release")
    if re.fullmatch(r"sha256-[a-f0-9]{64}", str(report.get("buildId", ""))) is None:
        errors.append("canary report requires a build ID")
    try:
        observed = dt.datetime.fromisoformat(str(report.get("observedAt")).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("timezone required")
        age = dt.datetime.now(dt.timezone.utc) - observed.astimezone(dt.timezone.utc)
        if age < dt.timedelta(0) or age > dt.timedelta(days=7):
            errors.append("canary report must be no older than seven days")
    except ValueError:
        errors.append("canary report requires a timezone-aware observedAt")
    devices = report.get("devices")
    if not isinstance(devices, list) or len(devices) < 2:
        errors.append("canary report requires at least two distinct devices")
        devices = []
    device_ids: set[str] = set()
    for index, device in enumerate(devices):
        if not isinstance(device, dict):
            errors.append(f"canary device {index} is invalid")
            continue
        device_id = device.get("deviceEvidenceId")
        if not _meaningful_string(device_id, maximum=160) or device_id in device_ids:
            errors.append(f"canary device {index} has an invalid or duplicate evidence ID")
        else:
            device_ids.add(device_id)
        for check in (
            "controlReady",
            "mediaReady",
            "safeStopConfirmed",
            "powerLossRecovered",
            "rollbackConfirmed",
        ):
            if device.get(check) is not True:
                errors.append(f"canary device {index} did not pass {check}")
        if not _finite_number(device.get("soakHours")) or device["soakHours"] < 2:
            errors.append(f"canary device {index} requires at least two soak hours")
        metrics = device.get("metrics")
        if not isinstance(metrics, dict):
            errors.append(f"canary device {index} requires metrics")
            continue
        limits = {
            "commandSuccessRate": (0.99, 1.0),
            "mediaAvailability": (0.99, 1.0),
            "stopP99Ms": (0.0, 150.0),
            "reconnectRecoveryP99Sec": (0.0, 60.0),
        }
        for metric, (minimum, maximum) in limits.items():
            value = metrics.get(metric)
            if not _finite_number(value) or not minimum <= value <= maximum:
                errors.append(
                    f"canary device {index} metric {metric} must be between {minimum} and {maximum}"
                )
    reviewer = report.get("reviewer")
    if not _meaningful_string(reviewer):
        errors.append("canary report requires an independent reviewer")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
