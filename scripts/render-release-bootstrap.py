#!/usr/bin/env python3
"""Render the short, version-bound production installer shipped with a release."""

from __future__ import annotations

import argparse
import base64
import re
from pathlib import Path

MARKERS = {
    "__BOTPARTY_RELEASE_REF__": "ref",
    "__BOTPARTY_RELEASE_ALLOWED_SIGNERS_BASE64__": "allowed_signers",
    "__BOTPARTY_RELEASE_BUNDLES_BASE64__": "bundles",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--allowed-signers", type=Path, required=True)
    parser.add_argument(
        "--bundle",
        action="append",
        metavar="ARCH,URL,SHA256",
        required=True,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.ref):
        raise SystemExit("--ref must be a lowercase, full commit ID")
    bundles: list[str] = []
    seen_architectures: set[str] = set()
    for value in args.bundle:
        try:
            architecture, url, digest = value.split(",", 2)
        except ValueError as error:
            raise SystemExit("--bundle must use ARCH,URL,SHA256") from error
        if architecture not in {"amd64", "arm64", "armv7"}:
            raise SystemExit(f"unsupported bundle architecture: {architecture}")
        if architecture in seen_architectures:
            raise SystemExit(f"duplicate bundle architecture: {architecture}")
        if not url.startswith("https://"):
            raise SystemExit("bundle URLs must use HTTPS")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SystemExit("bundle digests must be lowercase SHA-256 values")
        seen_architectures.add(architecture)
        bundles.append(f"{architecture}\t{url}\t{digest}")

    signer_bytes = args.allowed_signers.read_bytes()
    if not signer_bytes.strip():
        raise SystemExit("--allowed-signers must not be empty")
    values = {
        "ref": args.ref,
        "allowed_signers": base64.b64encode(signer_bytes).decode("ascii"),
        "bundles": base64.b64encode("\n".join(bundles).encode()).decode("ascii"),
    }
    rendered = args.template.read_text(encoding="utf-8")
    for marker, name in MARKERS.items():
        if rendered.count(marker) != 1:
            raise SystemExit(f"template marker is missing: {marker}")
        rendered = rendered.replace(marker, values[name])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    args.output.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
