from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from botparty_robot.artifacts import (
    MAX_MANIFEST_BYTES,
    ArtifactManifest,
    ArtifactVerificationError,
    _read_limited_descriptor,
    _read_url,
    _trusted_owner_uid,
    _validate_elf_header,
    install_streamer_manifest,
    load_public_key,
    main,
    normalized_arch,
    verify_installed_streamer,
)


def _sign(payload: dict[str, object]) -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = dict(payload)
    envelope["signature"] = base64.b64encode(private.sign(signed)).decode()
    return (
        json.dumps(envelope).encode(),
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw),
    )


def _payload() -> dict[str, object]:
    return {
        "version": "1.2.3",
        "platform": "linux",
        "arch": normalized_arch(),
        "url": "https://releases.example/streamer",
        "size": 4,
        "sha256": "0" * 64,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", "bad/version", "version"),
        ("platform", "darwin", "platform"),
        ("arch", "mips", "architecture"),
        ("size", 0, "size"),
        ("size", 101 * 1024 * 1024, "size"),
        ("sha256", "invalid", "SHA-256"),
        ("url", "http://releases.example/file", "HTTPS"),
        ("url", "https://user:pass@releases.example/file", "HTTPS"),
    ],
)
def test_artifact_manifest_rejects_invalid_trust_fields(field, value, message) -> None:
    payload = _payload()
    payload[field] = value
    raw, public = _sign(payload)
    with pytest.raises(ArtifactVerificationError, match=message):
        ArtifactManifest.from_signed_json(raw, public)


def test_artifact_manifest_rejects_invalid_envelope_shapes() -> None:
    payload = _payload()
    raw, public = _sign(payload)
    with pytest.raises(ArtifactVerificationError, match="64 KiB"):
        ArtifactManifest.from_signed_json(b" " * (MAX_MANIFEST_BYTES + 1), public)
    with pytest.raises(ArtifactVerificationError, match="valid JSON"):
        ArtifactManifest.from_signed_json(b"{", public)
    with pytest.raises(ArtifactVerificationError, match="object"):
        ArtifactManifest.from_signed_json(b"[]", public)
    without_signature = json.loads(raw)
    without_signature.pop("signature")
    with pytest.raises(ArtifactVerificationError, match="no Ed25519"):
        ArtifactManifest.from_signed_json(json.dumps(without_signature).encode(), public)
    payload["extra"] = True
    extra, extra_public = _sign(payload)
    with pytest.raises(ArtifactVerificationError, match="exactly"):
        ArtifactManifest.from_signed_json(extra, extra_public)
    invalid_values = _payload()
    invalid_values["size"] = None
    invalid_raw, invalid_public = _sign(invalid_values)
    with pytest.raises(ArtifactVerificationError, match="invalid values"):
        ArtifactManifest.from_signed_json(invalid_raw, invalid_public)


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "amd64"),
        ("AMD64", "amd64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("armv7l", "armv7"),
        ("armv7", "armv7"),
    ],
)
def test_architecture_normalization_is_explicit(machine, expected) -> None:
    assert normalized_arch(machine) == expected
    with pytest.raises(ArtifactVerificationError, match="unsupported"):
        normalized_arch("mips64")


class _Response:
    def __init__(self, data: bytes, final_url: str) -> None:
        self.data = data
        self.final_url = final_url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, _limit: int) -> bytes:
        return self.data


def test_artifact_download_rejects_insecure_redirect_and_oversize(monkeypatch) -> None:
    with pytest.raises(ArtifactVerificationError, match="HTTPS"):
        _read_url("http://releases.example/file", 4, 1)
    monkeypatch.setattr(
        "botparty_robot.artifacts.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(b"ok", "http://redirect.example/file"),
    )
    with pytest.raises(ArtifactVerificationError, match="redirect"):
        _read_url("https://releases.example/file", 4, 1)
    monkeypatch.setattr(
        "botparty_robot.artifacts.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(b"12345", "https://releases.example/file"),
    )
    with pytest.raises(ArtifactVerificationError, match="size limit"):
        _read_url("https://releases.example/file", 4, 1)


def test_artifact_download_accepts_bounded_https_response(monkeypatch) -> None:
    monkeypatch.setattr(
        "botparty_robot.artifacts.urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(b"1234", "https://releases.example/file"),
    )
    assert _read_url("https://releases.example/file", 4, 1) == b"1234"


def test_trusted_owner_configuration_is_closed(monkeypatch) -> None:
    assert _trusted_owner_uid(123) == 123
    monkeypatch.setenv("BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID", "456")
    assert _trusted_owner_uid() == 456
    monkeypatch.setenv("BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID", "not-a-uid")
    with pytest.raises(ArtifactVerificationError, match="numeric uid"):
        _trusted_owner_uid()
    monkeypatch.setenv("BOTPARTY_TRUSTED_ARTIFACT_OWNER_UID", "-1")
    with pytest.raises(ArtifactVerificationError, match="must not be negative"):
        _trusted_owner_uid()


def test_limited_descriptor_and_elf_header_fail_closed(tmp_path, monkeypatch) -> None:
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"12345")
    descriptor = os.open(oversized, os.O_RDONLY)
    try:
        with pytest.raises(ArtifactVerificationError, match="size limit"):
            _read_limited_descriptor(descriptor, 4, "fixture")
    finally:
        os.close(descriptor)

    binary = bytearray(Path("/bin/true").read_bytes())
    monkeypatch.setattr("botparty_robot.artifacts.normalized_arch", lambda *_args: "amd64")
    for name, mutate, message in (
        ("short", lambda data: data.__setitem__(slice(None), b"short"), "not an ELF"),
        ("byte-order", lambda data: data.__setitem__(5, 0), "byte order"),
        ("machine", lambda data: data.__setitem__(slice(18, 20), b"\x00\x00"), "architecture"),
    ):
        candidate = bytearray(binary)
        mutate(candidate)
        path = tmp_path / name
        path.write_bytes(candidate)
        descriptor = os.open(path, os.O_RDONLY)
        try:
            with pytest.raises(ArtifactVerificationError, match=message):
                _validate_elf_header(descriptor)
        finally:
            os.close(descriptor)


def test_public_key_and_installed_binary_metadata_are_fail_closed(tmp_path) -> None:
    key = tmp_path / "key"
    key.write_text("not-base64", encoding="ascii")
    key.chmod(0o600)
    with pytest.raises(ArtifactVerificationError, match="read"):
        load_public_key(key)
    key.write_text(base64.b64encode(b"short").decode(), encoding="ascii")
    with pytest.raises(ArtifactVerificationError, match="32 bytes"):
        load_public_key(key)
    valid_key = b"k" * 32
    key.write_text(base64.b64encode(valid_key).decode(), encoding="ascii")
    assert load_public_key(key) == valid_key
    key.unlink()
    key.symlink_to(tmp_path / "missing")
    with pytest.raises(ArtifactVerificationError, match="open trusted"):
        load_public_key(key)

    binary = tmp_path / "botparty-streamer"
    with pytest.raises(ArtifactVerificationError, match="missing"):
        verify_installed_streamer(binary)
    trusted_binary = Path("/bin/true").read_bytes()
    binary.write_bytes(trusted_binary)
    binary.chmod(0o600)
    with pytest.raises(ArtifactVerificationError, match="executable"):
        verify_installed_streamer(binary, hashlib.sha256(trusted_binary).hexdigest())
    binary.chmod(0o722)
    with pytest.raises(ArtifactVerificationError, match="not group/world writable"):
        verify_installed_streamer(binary, hashlib.sha256(trusted_binary).hexdigest())
    binary.chmod(0o700)
    with pytest.raises(ArtifactVerificationError, match="open trusted"):
        verify_installed_streamer(binary)
    with pytest.raises(ArtifactVerificationError, match="expected SHA-256"):
        verify_installed_streamer(binary, "bad")
    binary.unlink()
    real_binary = tmp_path / "real-streamer"
    real_binary.write_bytes(trusted_binary)
    real_binary.chmod(0o700)
    binary.symlink_to(real_binary)
    with pytest.raises(ArtifactVerificationError, match="open trusted"):
        verify_installed_streamer(binary, hashlib.sha256(trusted_binary).hexdigest())


def test_digest_sidecar_must_be_private_regular_and_valid(tmp_path) -> None:
    binary = tmp_path / "botparty-streamer"
    binary.write_bytes(Path("/bin/true").read_bytes())
    binary.chmod(0o700)
    sidecar = tmp_path / "botparty-streamer.sha256"
    sidecar.write_text("bad\n", encoding="ascii")
    sidecar.chmod(0o600)
    with pytest.raises(ArtifactVerificationError, match="expected SHA-256"):
        verify_installed_streamer(binary)
    sidecar.chmod(0o666)
    with pytest.raises(ArtifactVerificationError, match="not group/world writable"):
        verify_installed_streamer(binary)


def test_production_artifact_owner_defaults_to_root(monkeypatch, tmp_path) -> None:
    binary = tmp_path / "botparty-streamer"
    binary.write_bytes(Path("/bin/true").read_bytes())
    binary.chmod(0o700)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    monkeypatch.setenv("BOTPARTY_STREAMER_DIR", str(tmp_path))
    if os.geteuid() != 0:
        with pytest.raises(ArtifactVerificationError, match="owned by uid 0"):
            verify_installed_streamer(binary, digest)


@pytest.mark.parametrize("failure", ["version", "arch", "length", "digest", "elf", "dir"])
def test_manifest_install_faults_do_not_activate_target(tmp_path, monkeypatch, failure) -> None:
    binary = b"\x7fELF"
    manifest = ArtifactManifest(
        version="1.2.3",
        platform="linux",
        arch=normalized_arch(),
        url="https://releases.example/streamer",
        size=len(binary),
        sha256=hashlib.sha256(binary).hexdigest(),
    )
    expected_version = None
    delivered = binary
    if failure == "version":
        expected_version = "9.9.9"
    elif failure == "arch":
        manifest = replace(manifest, arch="armv7")
        if normalized_arch() == "armv7":
            manifest = replace(manifest, arch="amd64")
    elif failure == "length":
        delivered = binary[:-1]
    elif failure == "digest":
        delivered = b"\x7fELX"
    elif failure == "elf":
        delivered = b"NOPE"
        manifest = replace(manifest, sha256=hashlib.sha256(delivered).hexdigest())
    install_dir = tmp_path / "bin"
    if failure == "dir":
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        install_dir.symlink_to(real_dir)
    monkeypatch.setattr("botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: delivered)
    with pytest.raises(ArtifactVerificationError):
        install_streamer_manifest(manifest, install_dir, expected_version)
    assert not (install_dir / "botparty-streamer").exists()


def test_artifact_cli_rejects_half_custom_configuration(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as rejected:
        main(["--manifest-url", "https://example/manifest", "--dir", str(tmp_path)])
    assert rejected.value.code == 1
    assert "requires both" in capsys.readouterr().err


def test_artifact_cli_prints_successful_pinned_target(tmp_path, monkeypatch, capsys) -> None:
    target = tmp_path / "botparty-streamer"
    monkeypatch.setattr("botparty_robot.artifacts.install_pinned_streamer", lambda *_args: target)
    assert main(["--dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(target)


def test_help_smoke_fallback_must_succeed(tmp_path, monkeypatch) -> None:
    binary = b"\x7fELF"
    manifest = ArtifactManifest(
        version="1.2.3",
        platform="linux",
        arch=normalized_arch(),
        url="https://releases.example/streamer",
        size=len(binary),
        sha256=hashlib.sha256(binary).hexdigest(),
    )
    calls = 0

    def smoke(*_args: object, **_kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr("botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: binary)
    monkeypatch.setattr("botparty_robot.artifacts.run_sandboxed", smoke)
    target = install_streamer_manifest(manifest, tmp_path / "bin")
    assert target.is_file()
    assert calls == 2


@pytest.mark.parametrize("failure", ["version-smoke", "help-smoke", "writable-dir", "bad-target"])
def test_streamer_activation_rejects_local_policy_and_smoke_failures(
    tmp_path, monkeypatch, failure
) -> None:
    binary = b"\x7fELF"
    manifest = ArtifactManifest(
        version="1.2.3",
        platform="linux",
        arch=normalized_arch(),
        url="https://releases.example/streamer",
        size=len(binary),
        sha256=hashlib.sha256(binary).hexdigest(),
    )
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    monkeypatch.setattr("botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: binary)
    if failure == "writable-dir":
        install_dir.chmod(0o770)
    elif failure == "bad-target":
        (install_dir / "botparty-streamer").mkdir()
    elif failure == "version-smoke":
        monkeypatch.setattr(
            "botparty_robot.artifacts.run_sandboxed",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="9.9.9\n"),
        )
    else:
        monkeypatch.setattr(
            "botparty_robot.artifacts.run_sandboxed",
            lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout=""),
        )
    with pytest.raises(ArtifactVerificationError):
        install_streamer_manifest(manifest, install_dir)


def test_artifact_cli_uses_custom_manifest_path(tmp_path, monkeypatch, capsys) -> None:
    target = tmp_path / "botparty-streamer"
    observed = {}

    def custom(*args):
        observed["args"] = args
        return target

    monkeypatch.setattr("botparty_robot.artifacts.install_streamer", custom)
    assert (
        main(
            [
                "--manifest-url",
                "https://releases.example/manifest",
                "--public-key",
                str(tmp_path / "key"),
                "--dir",
                str(tmp_path),
                "--version",
                "1.2.3",
            ]
        )
        == 0
    )
    assert observed["args"][0] == "https://releases.example/manifest"
    assert capsys.readouterr().out.strip() == str(target)
