#!/usr/bin/env python3
"""Fail on high-confidence secrets in version-controlled files."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
}


def main() -> int:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    findings: list[str] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = Path(raw_path.decode("utf-8"))
        try:
            data = path.read_bytes()
            if b"\0" in data:
                continue
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "secret-scan: allow-test-fixture" in line:
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append(f"{path}:{line_number}: {name}")
    if findings:
        print("\n".join(findings))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
