#!/usr/bin/env python3
"""Require executable Python lines added by a change to be covered."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_added_lines(diff: str) -> dict[str, set[int]]:
    added: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line.removeprefix("+++ b/")
            continue
        match = HUNK.match(line)
        if match is None or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        added[current].update(range(start, start + count))
    return dict(added)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--base", required=True)
    parser.add_argument("--floor", type=float, default=90.0)
    args = parser.parse_args()
    if not 0 <= args.floor <= 100:
        parser.error("--floor must be between 0 and 100")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-ext-diff",
            args.base,
            "HEAD",
            "--",
            "botparty_robot/*.py",
            "botparty_robot/**/*.py",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    added = parse_added_lines(result.stdout)
    report = json.loads(args.coverage_json.read_text(encoding="utf-8"))
    files = report.get("files", {})
    executable = 0
    covered = 0
    uncovered: list[str] = []
    for filename, changed_lines in sorted(added.items()):
        details = files.get(filename)
        if not isinstance(details, dict):
            continue
        executed = {int(line) for line in details.get("executed_lines", [])}
        missing = {int(line) for line in details.get("missing_lines", [])}
        relevant = changed_lines & (executed | missing)
        executable += len(relevant)
        covered += len(relevant & executed)
        uncovered.extend(f"{filename}:{line}" for line in sorted(relevant & missing))
    percentage = 100.0 if executable == 0 else covered * 100 / executable
    print(
        f"diff coverage: {covered}/{executable} lines = {percentage:.2f}% (floor {args.floor:.2f}%)"
    )
    if percentage + 1e-9 < args.floor:
        print("uncovered added lines: " + ", ".join(uncovered[:100]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
