#!/usr/bin/env python3
"""Fail CI when delivery-control files contain accidental or unsafe content."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CONTEXT_FILES = (
    "pyproject.toml",
    "requirements/build-toolchain.txt",
    "requirements/dev.txt",
    "requirements/installer.txt",
    "scripts/install-botparty-client.sh",
    "scripts/render-release-bootstrap.py",
    "tests/installer/Dockerfile",
    "tests/installer/run.sh",
)


def main() -> int:
    dockerignore = ROOT / ".dockerignore"
    lines = dockerignore.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    if any(marker in line for line in lines for marker in ("*** Begin Patch", "*** End Patch")):
        errors.append(".dockerignore contains patch markers")
    if len(lines) != len(set(lines)):
        errors.append(".dockerignore contains duplicate patterns")
    for path in REQUIRED_CONTEXT_FILES:
        if not (ROOT / path).is_file():
            errors.append(f"required installer context file is missing: {path}")
        if path in lines:
            errors.append(f"required installer context file is ignored: {path}")
    required_ignores = {".git", "dist", "htmlcov", "reports", "*.egg-info"}
    missing = required_ignores - set(lines)
    if missing:
        errors.append(f".dockerignore misses: {', '.join(sorted(missing))}")
    if errors:
        sys.stderr.write("\n".join(errors) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
