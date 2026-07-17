"""Verification and atomic installation for native BotParty release artifacts."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_MANIFEST_BYTES = 64 * 1024
MAX_STREAMER_BYTES = 100 * 1024 * 1024


class ArtifactVerificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactManifest:
    version: str
    platform: str
    arch: str
    url: str
    size: int
    sha256: str

    @classmethod
    def from_signed_json(cls, raw: bytes, public_key: bytes) -> ArtifactManifest:
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ArtifactVerificationError("artifact manifest exceeds 64 KiB")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactVerificationError("artifact manifest is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ArtifactVerificationError("artifact manifest must be a JSON object")

        signature_raw = payload.pop("signature", None)
        if not isinstance(signature_raw, str):
            raise ArtifactVerificationError("artifact manifest has no Ed25519 signature")
        signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        try:
            signature = base64.b64decode(signature_raw, validate=True)
            Ed25519PublicKey.from_public_bytes(public_key).verify(signature, signed)
        except Exception as exc:
            raise ArtifactVerificationError("artifact manifest signature is invalid") from exc

        required = {"version", "platform", "arch", "url", "size", "sha256"}
        if set(payload) != required:
            raise ArtifactVerificationError(
                f"artifact manifest fields must be exactly: {', '.join(sorted(required))}"
            )
        try:
            manifest = cls(
                version=str(payload["version"]),
                platform=str(payload["platform"]),
                arch=str(payload["arch"]),
                url=str(payload["url"]),
                size=int(payload["size"]),
                sha256=str(payload["sha256"]).lower(),
            )
        except (TypeError, ValueError) as exc:
            raise ArtifactVerificationError("artifact manifest contains invalid values") from exc
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if (
            not self.version
            or len(self.version) > 128
            or any(character in self.version for character in "\r\n\x00/\\")
        ):
            raise ArtifactVerificationError("artifact version is invalid")
        if self.platform != "linux":
            raise ArtifactVerificationError(f"unsupported artifact platform: {self.platform}")
        if self.arch not in {"amd64", "arm64", "armv7"}:
            raise ArtifactVerificationError(f"unsupported artifact architecture: {self.arch}")
        if self.size <= 0 or self.size > MAX_STREAMER_BYTES:
            raise ArtifactVerificationError("artifact size is outside the allowed range")
        if len(self.sha256) != 64 or any(char not in "0123456789abcdef" for char in self.sha256):
            raise ArtifactVerificationError("artifact SHA-256 is invalid")
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ArtifactVerificationError("artifact URL must be HTTPS without user credentials")


PINNED_STREAMER_RELEASES = {
    "amd64": ArtifactManifest(
        version="v0.1.3",
        platform="linux",
        arch="amd64",
        url="https://dl.botparty.live/botparty-streamer-v0.1.3-linux-amd64",
        size=19_218_616,
        sha256="b7f4ff2660eed06c0a6f686cbc0c5c6eccbbd24fb414b53f4da89123c5bef78e",
    ),
    "arm64": ArtifactManifest(
        version="v0.1.3",
        platform="linux",
        arch="arm64",
        url="https://dl.botparty.live/botparty-streamer-v0.1.3-linux-arm64",
        size=18_350_264,
        sha256="38b4d3d8320106893d68f0d01b90ac416acb84c7d4f595dcd17bacb2e2ef0c4a",
    ),
    "armv7": ArtifactManifest(
        version="v0.1.3",
        platform="linux",
        arch="armv7",
        url="https://dl.botparty.live/botparty-streamer-v0.1.3-linux-arm",
        size=18_415_800,
        sha256="02e0173a1493740361ea4286d6b66c5a8583142b60d0f233ac170bb1f02fb558",
    ),
}


def normalized_arch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armv7",
        "armv7": "armv7",
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise ArtifactVerificationError(f"unsupported local architecture: {value}") from exc


def _read_url(url: str, limit: int, timeout: float) -> bytes:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ArtifactVerificationError("download URL must be HTTPS without user credentials")
    request = urllib.request.Request(url, headers={"User-Agent": "botparty-artifact-installer/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        final = urlsplit(response.geturl())
        if final.scheme != "https" or not final.hostname or final.username or final.password:
            raise ArtifactVerificationError("download redirect did not remain on secure HTTPS")
        data = bytes(response.read(limit + 1))
    if len(data) > limit:
        raise ArtifactVerificationError("download exceeds its size limit")
    return data


def load_public_key(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("public key is not a regular file")
        value = path.read_text(encoding="ascii").strip()
        key = base64.b64decode(value, validate=True)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ArtifactVerificationError("could not read the Ed25519 public key") from exc
    if len(key) != 32:
        raise ArtifactVerificationError("Ed25519 public key must contain exactly 32 bytes")
    return key


def _atomic_write(path: Path, value: str, encoding: str) -> None:
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


def install_streamer(
    manifest_url: str,
    public_key_path: Path,
    install_dir: Path,
    expected_version: str | None = None,
) -> Path:
    public_key = load_public_key(public_key_path)
    manifest = ArtifactManifest.from_signed_json(
        _read_url(manifest_url, MAX_MANIFEST_BYTES, timeout=10),
        public_key,
    )
    return install_streamer_manifest(manifest, install_dir, expected_version)


def install_pinned_streamer(
    install_dir: Path,
    expected_version: str | None = None,
) -> Path:
    arch = normalized_arch()
    try:
        manifest = PINNED_STREAMER_RELEASES[arch]
    except KeyError as exc:
        raise ArtifactVerificationError(f"no pinned streamer release for {arch}") from exc
    return install_streamer_manifest(manifest, install_dir, expected_version)


def install_streamer_manifest(
    manifest: ArtifactManifest,
    install_dir: Path,
    expected_version: str | None = None,
) -> Path:
    manifest.validate()
    if manifest.arch != normalized_arch():
        raise ArtifactVerificationError(
            f"artifact architecture {manifest.arch} does not match this host"
        )
    if expected_version and manifest.version.lstrip("v") != expected_version.lstrip("v"):
        raise ArtifactVerificationError(
            f"manifest version {manifest.version} does not match {expected_version}"
        )

    binary = _read_url(manifest.url, manifest.size, timeout=60)
    if len(binary) != manifest.size:
        raise ArtifactVerificationError("artifact length differs from the signed manifest")
    digest = hashlib.sha256(binary).hexdigest()
    if digest != manifest.sha256:
        raise ArtifactVerificationError("artifact SHA-256 differs from the signed manifest")
    if not binary.startswith(b"\x7fELF"):
        raise ArtifactVerificationError("streamer artifact is not an ELF executable")

    install_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    directory_metadata = install_dir.lstat()
    if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(directory_metadata.st_mode):
        raise ArtifactVerificationError("streamer install directory is not a regular directory")
    if (
        directory_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(directory_metadata.st_mode) & 0o022
    ):
        raise ArtifactVerificationError(
            "streamer install directory must be owner-controlled and not group/world writable"
        )
    target = install_dir / "botparty-streamer"
    if target.exists() or target.is_symlink():
        target_metadata = target.lstat()
        if stat.S_ISLNK(target_metadata.st_mode) or not stat.S_ISREG(target_metadata.st_mode):
            raise ArtifactVerificationError("existing streamer target is not a regular file")
    with tempfile.NamedTemporaryFile(dir=install_dir, prefix=".streamer-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(binary)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)
        version_smoke = subprocess.run(
            [str(temporary), "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        reported_version = version_smoke.stdout.strip()
        if reported_version:
            if version_smoke.returncode != 0 or reported_version.lstrip(
                "v"
            ) != manifest.version.lstrip("v"):
                raise ArtifactVerificationError("streamer version smoke test failed")
        else:
            help_smoke = subprocess.run(
                [str(temporary), "--help"],
                capture_output=True,
                check=False,
                timeout=5,
            )
            if help_smoke.returncode != 0:
                raise ArtifactVerificationError("streamer smoke test failed")

        previous = target.with_suffix(".previous")
        if target.exists():
            shutil.copy2(target, previous)
        os.replace(temporary, target)
        _atomic_write(
            install_dir / "botparty-streamer.version",
            f"{manifest.version}\n",
            "utf-8",
        )
        _atomic_write(
            install_dir / "botparty-streamer.sha256",
            f"{manifest.sha256}\n",
            "ascii",
        )
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install a verified BotParty streamer artifact")
    parser.add_argument("--manifest-url")
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--dir", type=Path, required=True)
    parser.add_argument("--version")
    args = parser.parse_args(argv)
    try:
        if bool(args.manifest_url) != bool(args.public_key):
            raise ArtifactVerificationError(
                "custom installation requires both manifest URL and public key"
            )
        if args.manifest_url and args.public_key:
            target = install_streamer(
                args.manifest_url,
                args.public_key,
                args.dir,
                args.version,
            )
        else:
            target = install_pinned_streamer(args.dir, args.version)
    except ArtifactVerificationError as exc:
        parser.exit(1, f"streamer install rejected: {exc}\n")
    sys.stdout.write(f"{target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
