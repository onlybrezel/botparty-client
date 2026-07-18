#!/usr/bin/env python3
"""Validate a long-running device soak report against bounded resource drift."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

RSS_BUDGETS = {"low": 350.0, "medium": 450.0, "reference": 550.0}
MAX_RAW_BYTES = 64 * 1024 * 1024


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def _slope_per_hour(samples: list[dict[str, object]], field: str) -> float:
    points = [
        (elapsed / 3600, measured)
        for sample in samples
        if (elapsed := _number(sample.get("elapsedSeconds"))) is not None
        and (measured := _number(sample.get(field))) is not None
    ]
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def _read_raw(path: Path, errors: list[str]) -> list[dict[str, object]]:
    if path.stat().st_size > MAX_RAW_BYTES:
        errors.append("soak raw data exceeds 64 MiB")
        return []
    samples: list[dict[str, object]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            sample = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"soak raw line {line_number} is invalid JSON")
            continue
        if not isinstance(sample, dict):
            errors.append(f"soak raw line {line_number} is not an object")
            continue
        for field in ("recordedAtMs", "elapsedSeconds", "rssMiB", "fds", "threads"):
            if _number(sample.get(field)) is None:
                errors.append(f"soak raw line {line_number} has invalid {field}")
        if not isinstance(sample.get("health"), dict):
            errors.append(f"soak raw line {line_number} has invalid health snapshot")
        samples.append(sample)
    return samples


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--raw", type=Path, required=True)
    args = parser.parse_args()
    errors: list[str] = []
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        raw_digest = hashlib.sha256(args.raw.read_bytes()).hexdigest()
        samples = _read_raw(args.raw, errors)
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        sys.stderr.write(f"invalid soak evidence: {exc}\n")
        return 1
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1:
        errors.append("unsupported soak schema")
        payload = {}
    if raw_digest != payload.get("rawDataSha256"):
        errors.append("soak raw-data digest does not match")
    if payload.get("rawDataFile") != args.raw.name:
        errors.append("soak report rawDataFile does not match supplied raw data")
    if not isinstance(payload.get("buildId"), str) or not payload["buildId"].strip():
        errors.append("soak report requires buildId")
    device_class = payload.get("deviceClass")
    if device_class not in RSS_BUDGETS:
        errors.append("soak report has unsupported deviceClass")
    duration = payload.get("durationHours")
    count = payload.get("sampleCount")
    if (duration_number := _number(duration)) is None or duration_number < 24:
        errors.append("soak duration must be at least 24 hours")
    if isinstance(count, bool) or not isinstance(count, int) or count < 288:
        errors.append("soak report requires at least 288 samples")
    if isinstance(count, int) and count != len(samples):
        errors.append("soak sampleCount does not match raw data")
    elapsed = [_number(sample.get("elapsedSeconds")) for sample in samples]
    if any(value is None for value in elapsed) or any(
        current <= previous
        for previous, current in itertools.pairwise(elapsed)
        if previous is not None and current is not None
    ):
        errors.append("soak elapsedSeconds must be strictly monotonic")
    if (
        elapsed
        and elapsed[-1] is not None
        and duration_number is not None
        and elapsed[-1] < duration_number * 3600 - 300
    ):
        errors.append("soak raw timeline does not cover reported duration")
    for field, maximum in (
        ("rssSlopeMiBPerHour", 1.0),
        ("fdSlopePerHour", 0.1),
        ("threadSlopePerHour", 0.1),
    ):
        value = payload.get(field)
        measured = _number(value)
        recomputed = _slope_per_hour(samples, field.removesuffix("SlopePerHour"))
        if field == "rssSlopeMiBPerHour":
            recomputed = _slope_per_hour(samples, "rssMiB")
        elif field == "fdSlopePerHour":
            recomputed = _slope_per_hour(samples, "fds")
        elif field == "threadSlopePerHour":
            recomputed = _slope_per_hour(samples, "threads")
        if measured is None or abs(measured) > maximum:
            errors.append(f"{field} exceeds {maximum}")
        elif abs(measured - recomputed) > 0.001:
            errors.append(f"{field} does not match raw data")
    if payload.get("healthErrorSamples") != 0:
        errors.append("soak contains health collection errors")
    raw_health_errors = sum(
        1
        for sample in samples
        if isinstance(sample.get("health"), dict) and "error" in sample["health"]
    )
    if payload.get("healthErrorSamples") != raw_health_errors:
        errors.append("healthErrorSamples does not match raw data")
    max_rss = _number(payload.get("maxRssMiB"))
    if device_class in RSS_BUDGETS and (
        max_rss is None or max_rss > RSS_BUDGETS[str(device_class)]
    ):
        errors.append("soak RSS exceeds device-class budget")
    max_temperature = _number(payload.get("maxTemperatureC"))
    if max_temperature is None or max_temperature > 85:
        errors.append("soak temperature is missing or exceeds 85 C")
    if (_number(payload.get("averageCameraFps")) or 0) <= 1:
        errors.append("soak camera did not sustain more than 1 FPS")
    if (_number(payload.get("cameraProgressRatio")) or 0) < 0.8:
        errors.append("soak camera progress ratio is below 0.8")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
