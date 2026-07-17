#!/usr/bin/env python3
"""Build a deterministic offline OTA bundle from a wheel and hashed lock."""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def _wheel_name_and_version(wheel: Path) -> tuple[str, str]:
    with zipfile.ZipFile(wheel) as archive:
        metadata_names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError("wheel must contain exactly one METADATA file")
        metadata = email.parser.BytesParser().parsebytes(archive.read(metadata_names[0]))
    name = metadata.get("Name", "").strip()
    version = metadata.get("Version", "").strip()
    if not name or not version or any(character in version for character in "\r\n/\\"):
        raise ValueError("wheel package metadata is invalid")
    return name, version


def _normalized_architecture() -> str:
    machine = platform.machine().lower()
    return {"x86_64": "amd64", "aarch64": "arm64", "armv7l": "armv7"}.get(machine, machine)


def _zip_entry(archive: zipfile.ZipFile, source: Path, destination: str) -> None:
    info = zipfile.ZipInfo(destination, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, source.read_bytes())


def build_bundle(wheel: Path, lock: Path, output: Path) -> Path:
    wheel = wheel.resolve(strict=True)
    lock = lock.resolve(strict=True)
    package_name, version = _wheel_name_and_version(wheel)
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="botparty-ota-") as temporary_name:
        temporary = Path(temporary_name)
        wheelhouse = temporary / "wheelhouse"
        wheelhouse.mkdir()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--quiet",
                "--require-hashes",
                "--dest",
                str(wheelhouse),
                "-r",
                str(lock),
            ],
            check=True,
            timeout=600,
        )
        app_wheel = wheelhouse / wheel.name
        app_wheel.write_bytes(wheel.read_bytes())
        requirements = temporary / "requirements.txt"
        lock_text = lock.read_text(encoding="utf-8").rstrip()
        app_requirement = f"{package_name}=={version} " + "\\\n" + f"    --hash=sha256:{wheel_hash}"
        requirements.write_text(
            f"{lock_text}\n{app_requirement}\n",
            encoding="utf-8",
        )
        staging = output.with_name(f".{output.name}.tmp")
        staging.unlink(missing_ok=True)
        try:
            with zipfile.ZipFile(staging, "w") as archive:
                _zip_entry(archive, requirements, "requirements.txt")
                for artifact in sorted(wheelhouse.iterdir(), key=lambda path: path.name):
                    if not artifact.is_file():
                        raise ValueError("OTA wheelhouse contains a non-file entry")
                    _zip_entry(archive, artifact, f"wheelhouse/{artifact.name}")
            staging.chmod(0o644)
            staging.replace(output)
        finally:
            staging.unlink(missing_ok=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a BotParty offline OTA bundle")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output
    if output is None:
        _name, version = _wheel_name_and_version(args.wheel)
        output = Path("dist") / f"botparty-robot-{version}-linux-{_normalized_architecture()}.zip"
    result = build_bundle(args.wheel, args.lock, output)
    sys.stdout.write(f"{result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
