#!/usr/bin/env python3
"""Measure reproducible package and CLI budgets and emit a JSON report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SIZE_BUDGETS = {
    "wheel_bytes": 2 * 1024 * 1024,
    "sdist_bytes": 3 * 1024 * 1024,
    "ota_bytes": 120 * 1024 * 1024,
    "production_dependencies": 40,
}
TIME_BUDGETS = {
    "help_seconds_p95": 2.5,
    "version_seconds_p95": 2.5,
    "config_validate_seconds_p95": 3.0,
}


def _measure(
    command: list[str], *, iterations: int, env: dict[str, str]
) -> tuple[float, list[float]]:
    samples: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        subprocess.run(command, check=True, capture_output=True, env=env, timeout=10)
        samples.append(time.perf_counter() - started)
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95 + 0.999) - 1))
    return ordered[index], samples


def _dependency_count(lock: Path) -> int:
    return sum(
        1
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line and not line[0].isspace() and "==" in line and not line.startswith("#")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--sdist", type=Path, required=True)
    parser.add_argument("--ota", type=Path)
    parser.add_argument("--lock", type=Path, default=Path("requirements/production.txt"))
    parser.add_argument("--config", type=Path, default=Path("config.example.yaml"))
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path)
    args = parser.parse_args()
    if not 3 <= args.iterations <= 20:
        parser.error("--iterations must be between 3 and 20")

    metrics: dict[str, float | int] = {
        "wheel_bytes": args.wheel.stat().st_size,
        "sdist_bytes": args.sdist.stat().st_size,
        "production_dependencies": _dependency_count(args.lock),
    }
    if args.ota is not None:
        metrics["ota_bytes"] = args.ota.stat().st_size

    samples: dict[str, list[float]] = {}
    with tempfile.TemporaryDirectory(prefix="botparty-performance-") as temporary_name:
        environment_path = Path(temporary_name) / "venv"
        subprocess.run(
            [sys.executable, "-m", "venv", str(environment_path)], check=True, timeout=60
        )
        python = environment_path / "bin" / "python"
        install = [str(python), "-m", "pip", "install", "--require-hashes"]
        if args.wheelhouse is not None:
            install.extend(["--no-index", "--find-links", str(args.wheelhouse.resolve())])
        subprocess.run([*install, "-r", str(args.lock.resolve())], check=True, timeout=600)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(args.wheel.resolve())],
            check=True,
            timeout=120,
        )
        env = dict(os.environ)
        env["BOTPARTY_CLAIM_TOKEN"] = "performance-check-placeholder"
        commands = {
            "help_seconds_p95": [str(python), "-m", "botparty_robot", "--help"],
            "version_seconds_p95": [str(python), "-m", "botparty_robot", "--version"],
            "config_validate_seconds_p95": [
                str(python),
                "-m",
                "botparty_robot",
                "--config",
                str(args.config.resolve()),
                "config",
                "validate",
            ],
        }
        for name, command in commands.items():
            p95, raw = _measure(command, iterations=args.iterations, env=env)
            metrics[name] = round(p95, 6)
            samples[name] = [round(value, 6) for value in raw]

    failures: list[str] = []
    for name, budget in {**SIZE_BUDGETS, **TIME_BUDGETS}.items():
        value = metrics.get(name)
        if value is not None and value > budget:
            failures.append(f"{name}={value} exceeds {budget}")
    wheel_digest = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    report = {
        "schemaVersion": 1,
        "artifact": {
            "wheel": args.wheel.name,
            "wheelSha256": wheel_digest,
            "buildId": f"sha256-{wheel_digest}",
            "python": platform.python_version(),
            "architecture": platform.machine(),
            "host": platform.platform(),
        },
        "metrics": metrics,
        "budgets": {**SIZE_BUDGETS, **TIME_BUDGETS},
        "samples": samples,
        "summary": {
            "passed": not failures,
            "sampleCount": args.iterations,
            "meanCliSeconds": round(
                statistics.fmean(value for values in samples.values() for value in values), 6
            ),
        },
        "failures": failures,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["metrics"], sort_keys=True))
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
