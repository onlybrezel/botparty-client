#!/usr/bin/env python3
"""Record a 24-hour-or-longer resource and health soak for one client process."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
import time
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


def _process_sample(pid: int) -> dict[str, object]:
    status: dict[str, str] = {}
    for line in Path(f"/proc/{pid}/status").read_text(encoding="ascii").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            status[key] = value.strip()
    rss_kib = int(status.get("VmRSS", "0 kB").split()[0])
    threads = int(status.get("Threads", "0"))
    fd_count = len(tuple(Path(f"/proc/{pid}/fd").iterdir()))
    temperatures = []
    for thermal in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            value = float(thermal.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
        temperatures.append(value / 1000 if value > 1000 else value)
    return {
        "rssMiB": round(rss_kib / 1024, 3),
        "threads": threads,
        "fds": fd_count,
        "temperatureC": round(max(temperatures), 2) if temperatures else None,
    }


def _health_sample(url: str) -> dict[str, object]:
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(url, timeout=3) as response:
            payload = response.read(256 * 1024 + 1)
            if len(payload) > 256 * 1024:
                raise ValueError("health response exceeds 256 KiB")
            decoded = json.loads(payload)
            if not isinstance(decoded, dict):
                raise ValueError("health response is not an object")
            stats = decoded.get("stats") if isinstance(decoded.get("stats"), dict) else {}
            return {
                "status": decoded.get("status"),
                "ready": decoded.get("ready"),
                "gatewayConnected": decoded.get("gatewayConnected"),
                "cameraFrames": stats.get("cameraFrames"),
                "reconnectAttempts": stats.get("reconnectAttempts"),
                "controlDisconnects": stats.get("controlDisconnects"),
                "mediaReconnects": stats.get("mediaReconnects"),
                "cameraTaskRestarts": stats.get("cameraTaskRestarts"),
            }
    except Exception as exc:
        return {"error": type(exc).__name__}


def _slope_per_hour(samples: list[dict[str, object]], field: str) -> float:
    points = [
        (float(sample["elapsedSeconds"]) / 3600, float(sample[field]))
        for sample in samples
        if isinstance(sample.get(field), (int, float))
    ]
    if len(points) < 2:
        return 0.0
    mean_x = sum(point[0] for point in points) / len(points)
    mean_y = sum(point[1] for point in points) / len(points)
    denominator = sum((point[0] - mean_x) ** 2 for point in points)
    if denominator == 0:
        return 0.0
    return sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--duration-hours", type=float, default=24)
    parser.add_argument("--interval-seconds", type=float, default=30)
    parser.add_argument("--health-url", default="http://127.0.0.1:9100/health")
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--device-class", choices=("low", "medium", "reference"), required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration_hours < 24:
        parser.error("--duration-hours must be at least 24")
    if not 5 <= args.interval_seconds <= 300:
        parser.error("--interval-seconds must be between 5 and 300")
    if args.pid <= 1 or not Path(f"/proc/{args.pid}").is_dir():
        parser.error("--pid must identify a running non-init process")
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.report_output.parent.mkdir(parents=True, exist_ok=True)
    started_wall = time.time()
    started = time.monotonic()
    deadline = started + args.duration_hours * 3600
    samples: list[dict[str, object]] = []
    with args.raw_output.open("x", encoding="utf-8") as raw:
        os.chmod(args.raw_output, 0o600)
        while time.monotonic() < deadline:
            sample = {
                "recordedAtMs": int(time.time() * 1000),
                "elapsedSeconds": round(time.monotonic() - started, 3),
                **_process_sample(args.pid),
                "health": _health_sample(args.health_url),
            }
            samples.append(sample)
            raw.write(json.dumps(sample, sort_keys=True, separators=(",", ":")) + "\n")
            raw.flush()
            os.fsync(raw.fileno())
            time.sleep(min(args.interval_seconds, max(0.0, deadline - time.monotonic())))
    digest = hashlib.sha256(args.raw_output.read_bytes()).hexdigest()
    health_samples = [
        sample["health"] for sample in samples if isinstance(sample.get("health"), dict)
    ]
    frame_points = [
        (float(sample["elapsedSeconds"]), int(health["cameraFrames"]))
        for sample, health in zip(samples, health_samples, strict=True)
        if isinstance(health.get("cameraFrames"), int)
        and not isinstance(health.get("cameraFrames"), bool)
    ]
    frame_progress = sum(
        1 for previous, current in itertools.pairwise(frame_points) if current[1] > previous[1]
    )
    frame_delta = frame_points[-1][1] - frame_points[0][1] if len(frame_points) >= 2 else 0
    frame_seconds = frame_points[-1][0] - frame_points[0][0] if len(frame_points) >= 2 else 0

    def counter_delta(field: str) -> int:
        values = [
            int(health[field])
            for health in health_samples
            if isinstance(health.get(field), int) and not isinstance(health.get(field), bool)
        ]
        return max(0, values[-1] - values[0]) if len(values) >= 2 else 0

    report = {
        "schemaVersion": 1,
        "buildId": args.build_id,
        "deviceClass": args.device_class,
        "startedAt": int(started_wall),
        "endedAt": int(time.time()),
        "durationHours": round((time.monotonic() - started) / 3600, 4),
        "sampleCount": len(samples),
        "rawDataFile": args.raw_output.name,
        "rawDataSha256": digest,
        "rssSlopeMiBPerHour": round(_slope_per_hour(samples, "rssMiB"), 6),
        "fdSlopePerHour": round(_slope_per_hour(samples, "fds"), 6),
        "threadSlopePerHour": round(_slope_per_hour(samples, "threads"), 6),
        "maxRssMiB": max(float(sample["rssMiB"]) for sample in samples),
        "maxFds": max(int(sample["fds"]) for sample in samples),
        "maxThreads": max(int(sample["threads"]) for sample in samples),
        "maxTemperatureC": max(
            (
                float(sample["temperatureC"])
                for sample in samples
                if sample["temperatureC"] is not None
            ),
            default=None,
        ),
        "healthErrorSamples": sum(
            1 for health in health_samples if isinstance(health, dict) and "error" in health
        ),
        "cameraFrameDelta": frame_delta,
        "averageCameraFps": round(frame_delta / frame_seconds, 3) if frame_seconds > 0 else 0,
        "cameraProgressRatio": round(frame_progress / max(1, len(frame_points) - 1), 6),
        "reconnectAttempts": counter_delta("reconnectAttempts"),
        "controlDisconnects": counter_delta("controlDisconnects"),
        "mediaReconnects": counter_delta("mediaReconnects"),
        "cameraTaskRestarts": counter_delta("cameraTaskRestarts"),
    }
    args.report_output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.chmod(args.report_output, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
