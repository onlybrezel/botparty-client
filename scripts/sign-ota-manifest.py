#!/usr/bin/env python3
"""Sign an OTA bundle manifest with an environment-provided Ed25519 key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def sign_manifest(
    bundle: Path, bundle_url: str, version: str, encoded_key: str
) -> dict[str, object]:
    parsed = urlsplit(bundle_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("bundle URL must be HTTPS without credentials")
    key_bytes = base64.b64decode(encoded_key.strip(), validate=True)
    if len(key_bytes) != 32:
        raise ValueError("release key must be a base64-encoded 32-byte Ed25519 private key")
    content = bundle.read_bytes()
    payload: dict[str, object] = {
        "version": version,
        "bundleUrl": bundle_url,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    payload["signature"] = base64.b64encode(
        Ed25519PrivateKey.from_private_bytes(key_bytes).sign(canonical)
    ).decode("ascii")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign a BotParty OTA manifest")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--bundle-url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private-key-env", default="OTA_ED25519_PRIVATE_KEY")
    args = parser.parse_args()
    encoded_key = os.environ.get(args.private_key_env, "")
    if not encoded_key:
        parser.error(f"environment variable {args.private_key_env} is required")
    manifest = sign_manifest(args.bundle, args.bundle_url, args.version, encoded_key)
    args.output.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
