#!/usr/bin/env python3
"""Validate local Markdown links without network access."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def main() -> int:
    failures: list[str] = []
    for document in [Path("README.md"), *Path("docs").rglob("*.md")]:
        for line_number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            for raw_target in LINK.findall(line):
                target = raw_target.split("#", 1)[0].strip()
                if not target or "://" in target or target.startswith("mailto:"):
                    continue
                resolved = (document.parent / unquote(target)).resolve()
                if not resolved.exists():
                    failures.append(f"{document}:{line_number}: missing {target}")
    if failures:
        print("\n".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
