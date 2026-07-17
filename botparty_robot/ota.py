"""Signed A/B release bundle installation and rollback markers."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .artifacts import ArtifactVerificationError, load_public_key
from .config import OtaConfig

MAX_OTA_MANIFEST_BYTES = 64 * 1024
MAX_OTA_BUNDLE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class OtaManifest:
    version: str
    bundle_url: str
    size: int
    sha256: str

    @classmethod
    def parse(cls, raw: bytes, public_key: bytes) -> OtaManifest:
        if len(raw) > MAX_OTA_MANIFEST_BYTES:
            raise ArtifactVerificationError("OTA manifest exceeds 64 KiB")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactVerificationError("OTA manifest is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ArtifactVerificationError("OTA manifest must be an object")
        signature_raw = payload.pop("signature", None)
        signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            if not isinstance(signature_raw, str):
                raise ValueError
            signature = base64.b64decode(signature_raw, validate=True)
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, signed)
        except Exception as exc:
            raise ArtifactVerificationError("OTA manifest signature is invalid") from exc
        if set(payload) != {"version", "bundleUrl", "size", "sha256"}:
            raise ArtifactVerificationError("OTA manifest fields are invalid")
        manifest = cls(
            version=str(payload["version"]),
            bundle_url=str(payload["bundleUrl"]),
            size=int(payload["size"]),
            sha256=str(payload["sha256"]).lower(),
        )
        if (
            not manifest.version
            or len(manifest.version) > 128
            or any(character in manifest.version for character in "\r\n\x00/\\")
        ):
            raise ArtifactVerificationError("OTA version is invalid")
        parsed = urlsplit(manifest.bundle_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ArtifactVerificationError("OTA bundle URL must be HTTPS without credentials")
        if manifest.size <= 0 or manifest.size > MAX_OTA_BUNDLE_BYTES:
            raise ArtifactVerificationError("OTA bundle size is outside the allowed range")
        if len(manifest.sha256) != 64 or any(
            char not in "0123456789abcdef" for char in manifest.sha256
        ):
            raise ArtifactVerificationError("OTA bundle SHA-256 is invalid")
        return manifest


def _download(url: str, limit: int, timeout: float) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ArtifactVerificationError("OTA URL must be HTTPS without credentials")
    request = urllib.request.Request(url, headers={"User-Agent": "botparty-ota/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or not final.hostname or final.username or final.password:
            raise ArtifactVerificationError("OTA redirect did not remain on secure HTTPS")
        data = bytes(response.read(limit + 1))
    if len(data) > limit:
        raise ArtifactVerificationError("OTA download exceeds its signed size")
    return data


class UpdateManager:
    def __init__(self, config: OtaConfig) -> None:
        if not config.enabled:
            raise ValueError("OTA is disabled")
        assert config.manifest_url is not None
        assert config.public_key_file is not None
        assert config.state_directory is not None
        self.manifest_url = config.manifest_url
        self.public_key_file = config.public_key_file
        self.state_directory = config.state_directory.expanduser().resolve()
        self.slots_directory = self.state_directory / "slots"
        self.current_link = self.state_directory / "current"
        self.pending_file = self.state_directory / "pending.json"
        self.boot_attempted_file = self.state_directory / "boot-attempted"

    def install(self, config_path: Path) -> Path:
        public_key = load_public_key(self.public_key_file)
        manifest = OtaManifest.parse(
            _download(self.manifest_url, MAX_OTA_MANIFEST_BYTES, 10),
            public_key,
        )
        bundle = _download(manifest.bundle_url, manifest.size, 120)
        if len(bundle) != manifest.size:
            raise ArtifactVerificationError("OTA bundle length differs from its manifest")
        if hashlib.sha256(bundle).hexdigest() != manifest.sha256:
            raise ArtifactVerificationError("OTA bundle SHA-256 differs from its manifest")

        self.slots_directory.mkdir(mode=0o750, parents=True, exist_ok=True)
        slot = self._inactive_slot()
        staging = self.slots_directory / f".{slot.name}.staging"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(mode=0o750)
        try:
            self._extract_bundle(bundle, staging)
            requirements = staging / "requirements.txt"
            wheelhouse = staging / "wheelhouse"
            if not requirements.is_file() or not wheelhouse.is_dir():
                raise ArtifactVerificationError(
                    "OTA bundle must contain requirements.txt and wheelhouse/"
                )
            subprocess.run(
                [sys.executable, "-m", "venv", str(staging / "venv")],
                check=True,
                timeout=60,
            )
            python = staging / "venv" / "bin" / "python"
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--require-hashes",
                    "--find-links",
                    str(wheelhouse),
                    "-r",
                    str(requirements),
                ],
                check=True,
                timeout=300,
            )
            subprocess.run(
                [str(python), "-m", "botparty_robot", "--version"],
                check=True,
                timeout=10,
            )
            subprocess.run(
                [
                    str(python),
                    "-m",
                    "botparty_robot",
                    "--config",
                    str(config_path),
                    "config",
                    "validate",
                ],
                check=True,
                timeout=20,
            )
            (staging / "release.json").write_text(
                json.dumps({"version": manifest.version, "sha256": manifest.sha256}),
                encoding="utf-8",
            )
            shutil.rmtree(slot, ignore_errors=True)
            os.replace(staging, slot)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

        previous = os.readlink(self.current_link) if self.current_link.is_symlink() else None
        pending = {
            "version": manifest.version,
            "previous": previous,
            "target": str(slot),
        }
        _atomic_text(self.pending_file, json.dumps(pending), "utf-8")
        temporary_link = self.state_directory / ".current.new"
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(slot)
        os.replace(temporary_link, self.current_link)
        _atomic_text(self.boot_attempted_file, "1\n", "ascii")
        return slot / "venv" / "bin" / "python"

    def confirm(self) -> None:
        self.pending_file.unlink(missing_ok=True)
        self.boot_attempted_file.unlink(missing_ok=True)

    def rollback_if_unconfirmed(self) -> bool:
        if not self.pending_file.is_file() or not self.boot_attempted_file.is_file():
            return False
        try:
            pending = json.loads(self.pending_file.read_text(encoding="utf-8"))
            previous = pending.get("previous")
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, str) and previous:
            temporary_link = self.state_directory / ".current.rollback"
            temporary_link.unlink(missing_ok=True)
            temporary_link.symlink_to(previous)
            os.replace(temporary_link, self.current_link)
        else:
            self.current_link.unlink(missing_ok=True)
        self.pending_file.unlink(missing_ok=True)
        self.boot_attempted_file.unlink(missing_ok=True)
        return True

    def _inactive_slot(self) -> Path:
        current = self.current_link.resolve() if self.current_link.is_symlink() else None
        slot_a = self.slots_directory / "a"
        slot_b = self.slots_directory / "b"
        return slot_b if current == slot_a.resolve() else slot_a

    def _extract_bundle(self, bundle: bytes, target: Path) -> None:
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(bundle)
            handle.flush()
            try:
                with zipfile.ZipFile(handle.name) as archive:
                    total = 0
                    for info in archive.infolist():
                        path = Path(info.filename)
                        if path.is_absolute() or ".." in path.parts:
                            raise ArtifactVerificationError("OTA bundle contains an unsafe path")
                        mode = info.external_attr >> 16
                        if stat.S_ISLNK(mode) or stat.S_ISBLK(mode) or stat.S_ISCHR(mode):
                            raise ArtifactVerificationError(
                                "OTA bundle contains a link or device entry"
                            )
                        total += info.file_size
                        if total > MAX_OTA_BUNDLE_BYTES:
                            raise ArtifactVerificationError("OTA bundle expands beyond 512 MiB")
                    for info in archive.infolist():
                        destination = target.joinpath(*Path(info.filename).parts)
                        if info.is_dir():
                            destination.mkdir(mode=0o750, parents=True, exist_ok=True)
                            continue
                        destination.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
                        with archive.open(info) as source, destination.open("xb") as output:
                            shutil.copyfileobj(source, output)
            except zipfile.BadZipFile as exc:
                raise ArtifactVerificationError("OTA bundle is not a ZIP archive") from exc


def rollback_state_directory(state_directory: Path) -> bool:
    pending_file = state_directory / "pending.json"
    attempted_file = state_directory / "boot-attempted"
    current_link = state_directory / "current"
    if not pending_file.is_file() or not attempted_file.is_file():
        return False
    try:
        pending = json.loads(pending_file.read_text(encoding="utf-8"))
        previous = pending.get("previous")
    except (OSError, json.JSONDecodeError):
        previous = None
    if isinstance(previous, str) and previous:
        temporary_link = state_directory / ".current.rollback"
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(previous)
        os.replace(temporary_link, current_link)
    else:
        current_link.unlink(missing_ok=True)
    pending_file.unlink(missing_ok=True)
    attempted_file.unlink(missing_ok=True)
    return True


def _atomic_text(path: Path, value: str, encoding: str) -> None:
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding=encoding) as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_boot(state_directory: Path) -> Path | None:
    state_directory = state_directory.resolve()
    pending_file = state_directory / "pending.json"
    attempted_file = state_directory / "boot-attempted"
    current_link = state_directory / "current"
    if pending_file.is_file() and attempted_file.is_file():
        rollback_state_directory(state_directory)
    elif pending_file.is_file():
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
            target = Path(pending["target"]).resolve()
            target.relative_to((state_directory / "slots").resolve())
        except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ArtifactVerificationError("OTA pending marker is invalid") from exc
        python = target / "venv" / "bin" / "python"
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ArtifactVerificationError("OTA pending slot has no executable Python")
        temporary_link = state_directory / ".current.prepare"
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(target)
        os.replace(temporary_link, current_link)
        _atomic_text(attempted_file, "1\n", "ascii")
        return python
    if current_link.is_symlink():
        python = current_link.resolve() / "venv" / "bin" / "python"
        if python.is_file() and os.access(python, os.X_OK):
            return python
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BotParty OTA boot helper")
    parser.add_argument("operation", choices=["prepare", "rollback"])
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.operation == "rollback":
        return 0 if rollback_state_directory(args.state.resolve()) else 3
    try:
        executable = prepare_boot(args.state)
    except ArtifactVerificationError as exc:
        parser.exit(1, f"OTA boot rejected: {exc}\n")
    if executable is None:
        return 3
    sys.stdout.write(f"{executable}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
