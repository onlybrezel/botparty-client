#!/usr/bin/env python3
"""Fail CI once compatibility code reaches its documented removal date."""

from __future__ import annotations

import argparse
import ast
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "botparty_robot" / "__main__.py"
DEADLINE_NAME = "LEGACY_CONFIG_DEPRECATION_DEADLINE"
LEGACY_FUNCTIONS = {
    "_apply_legacy_hardware_defaults",
    "_apply_legacy_video_defaults",
    "_apply_legacy_tts_defaults",
}


def _deadline_and_functions() -> tuple[date, set[str]]:
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"), filename=str(ENTRYPOINT))
    deadline: date | None = None
    functions: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.add(node.name)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id == DEADLINE_NAME
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    deadline = date.fromisoformat(node.value.value)
    if deadline is None:
        raise ValueError(f"{DEADLINE_NAME} is missing or not a literal ISO date")
    return deadline, functions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    deadline, functions = _deadline_and_functions()
    remaining = sorted(LEGACY_FUNCTIONS & functions)
    if args.as_of >= deadline and remaining:
        parser.error(
            f"legacy config removal deadline {deadline.isoformat()} reached; "
            f"remove: {', '.join(remaining)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
