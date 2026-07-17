#!/usr/bin/env python3
"""Generate a release license inventory from an installed Python environment."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

METADATA_SCRIPT = r"""
import json
from importlib.metadata import distributions

items = []
for distribution in distributions():
    metadata = distribution.metadata
    license_value = metadata.get("License-Expression") or metadata.get("License") or ""
    if not license_value or len(license_value) > 200 or "\n" in license_value:
        classifiers = metadata.get_all("Classifier") or []
        license_classifiers = [
            value.rsplit("::", 1)[-1].strip()
            for value in classifiers
            if value.startswith("License ::")
        ]
        license_value = ", ".join(license_classifiers) or "Not declared in package metadata"
    items.append({
        "name": metadata.get("Name") or distribution.name,
        "version": distribution.version,
        "license": license_value.strip(),
        "homepage": metadata.get("Home-page") or metadata.get("Project-URL") or "",
    })
print(json.dumps(items))
"""


def generate_inventory(python: Path) -> str:
    result = subprocess.run(
        [str(python), "-c", METADATA_SCRIPT],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    items = json.loads(result.stdout)
    if not isinstance(items, list) or not items:
        raise ValueError("installed environment returned no package metadata")
    rows = []
    for item in sorted(items, key=lambda entry: str(entry["name"]).lower()):
        name = str(item["name"]).replace("|", "\\|")
        version = str(item["version"]).replace("|", "\\|")
        license_value = str(item["license"]).replace("|", "\\|")
        homepage = str(item["homepage"]).replace("|", "\\|")
        rows.append(f"| {name} | {version} | {license_value} | {homepage} |")
    return "\n".join(
        (
            "# Third-party notices",
            "",
            "This inventory is generated from the exact installed release environment. "
            "Each component retains its own license.",
            "",
            "| Package | Version | Declared license | Project metadata |",
            "|---|---|---|---|",
            *rows,
            "",
            "Vendor firmware, hardware SDKs and cloud services are not relicensed by this project.",
            "",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate third-party release notices")
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(generate_inventory(args.python), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
