import base64
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from botparty_robot.artifacts import (
    PINNED_STREAMER_RELEASES,
    ArtifactManifest,
    ArtifactVerificationError,
    install_pinned_streamer,
    install_streamer,
    normalized_arch,
)
from botparty_robot.config import ServerConfig, StateConfig
from botparty_robot.device_state import DeviceStateError, load_or_create_device_key
from botparty_robot.ota import OtaManifest, UpdateManager, prepare_boot
from botparty_robot.protocol import ControlCommand, RemoteAction
from botparty_robot.video.base import BaseVideoProfile


def _signed_manifest(payload: dict[str, object]) -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = dict(payload)
    envelope["signature"] = base64.b64encode(private.sign(signed)).decode()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return json.dumps(envelope).encode(), public


def test_streamer_manifest_signature_and_exact_fields() -> None:
    payload = {
        "version": "1.2.3",
        "platform": "linux",
        "arch": "amd64",
        "url": "https://releases.example/streamer",
        "size": 4,
        "sha256": "0" * 64,
    }
    raw, public = _signed_manifest(payload)
    assert ArtifactManifest.from_signed_json(raw, public).version == "1.2.3"

    tampered = raw.replace(b"1.2.3", b"1.2.4")
    with pytest.raises(ArtifactVerificationError, match="signature"):
        ArtifactManifest.from_signed_json(tampered, public)


def test_invalid_streamer_download_keeps_previous_binary(tmp_path, monkeypatch) -> None:
    binary = b"\x7fELFbad"
    payload = {
        "version": "1.2.3",
        "platform": "linux",
        "arch": normalized_arch(),
        "url": "https://releases.example/streamer",
        "size": len(binary),
        "sha256": "0" * 64,
    }
    raw, public = _signed_manifest(payload)
    public_key = tmp_path / "release.pub"
    public_key.write_text(base64.b64encode(public).decode(), encoding="ascii")
    install_dir = tmp_path / "bin"
    install_dir.mkdir()
    target = install_dir / "botparty-streamer"
    target.write_bytes(b"previous-working-version")
    downloads = iter((raw, binary))
    monkeypatch.setattr(
        "botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: next(downloads)
    )
    with pytest.raises(ArtifactVerificationError, match="SHA-256"):
        install_streamer(
            "https://releases.example/manifest.json",
            public_key,
            install_dir,
        )
    assert target.read_bytes() == b"previous-working-version"


def test_valid_signed_streamer_is_smoke_tested_and_activated(tmp_path, monkeypatch) -> None:
    binary = Path("/bin/true").read_bytes()
    payload = {
        "version": "1.2.3",
        "platform": "linux",
        "arch": normalized_arch(),
        "url": "https://releases.example/streamer",
        "size": len(binary),
        "sha256": hashlib.sha256(binary).hexdigest(),
    }
    raw, public = _signed_manifest(payload)
    public_key = tmp_path / "release.pub"
    public_key.write_text(base64.b64encode(public).decode(), encoding="ascii")
    install_dir = tmp_path / "bin"
    downloads = iter((raw, binary))
    monkeypatch.setattr(
        "botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: next(downloads)
    )
    monkeypatch.setattr(
        "botparty_robot.artifacts.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v1.2.3\n"),
    )

    target = install_streamer(
        "https://releases.example/manifest.json",
        public_key,
        install_dir,
        expected_version="1.2.3",
    )

    assert target.read_bytes() == binary
    assert (install_dir / "botparty-streamer.version").read_text() == "1.2.3\n"
    assert (install_dir / "botparty-streamer.sha256").read_text().strip() == payload["sha256"]


def test_official_streamer_catalog_is_complete_and_default_install_uses_it(
    tmp_path, monkeypatch
) -> None:
    assert set(PINNED_STREAMER_RELEASES) == {"amd64", "arm64", "armv7"}
    for arch, release in PINNED_STREAMER_RELEASES.items():
        release.validate()
        assert release.arch == arch
        assert release.url.startswith("https://dl.botparty.live/")

    binary = Path("/bin/true").read_bytes()
    arch = normalized_arch()
    release = ArtifactManifest(
        version="v9.8.7",
        platform="linux",
        arch=arch,
        url="https://dl.botparty.live/test-streamer",
        size=len(binary),
        sha256=hashlib.sha256(binary).hexdigest(),
    )
    monkeypatch.setitem(PINNED_STREAMER_RELEASES, arch, release)
    monkeypatch.setattr("botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: binary)
    monkeypatch.setattr(
        "botparty_robot.artifacts.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="v9.8.7\n"),
    )

    target = install_pinned_streamer(tmp_path / "bin", expected_version="9.8.7")

    assert target.read_bytes() == binary
    assert target.with_suffix(".version").read_text() == "v9.8.7\n"


def test_managed_streamer_directory_uses_service_state(monkeypatch, tmp_path) -> None:
    profile = object.__new__(BaseVideoProfile)
    monkeypatch.setenv("BOTPARTY_STATE_DIR", str(tmp_path / "state"))
    assert profile.managed_streamer_dir() == tmp_path / "state" / "bin"

    monkeypatch.setenv("BOTPARTY_STREAMER_DIR", str(tmp_path / "custom"))
    assert profile.managed_streamer_dir() == tmp_path / "custom"


@pytest.mark.parametrize(
    "api_url,livekit_url",
    [
        ("http://example.com", "wss://example.com"),
        ("https://user:pass@example.com", "wss://example.com"),
        ("https://example.com", "ws://example.com"),
    ],
)
def test_nonlocal_or_credentialed_transport_is_rejected(api_url: str, livekit_url: str) -> None:
    with pytest.raises(ValidationError):
        ServerConfig(
            api_url=api_url,
            livekit_url=livekit_url,
            claim_token="claim-token",
            allow_insecure_dev_transport=True,
        )


def test_loopback_transport_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError):
        ServerConfig(
            api_url="http://127.0.0.1:3000",
            livekit_url="ws://127.0.0.1:7880",
            claim_token="claim-token",
        )
    config = ServerConfig(
        api_url="http://127.0.0.1:3000",
        livekit_url="ws://127.0.0.1:7880",
        claim_token="claim-token",
        allow_insecure_dev_transport=True,
    )
    assert config.api_url.startswith("http://")


def test_device_key_is_stable_private_and_rejects_symlinks(tmp_path) -> None:
    state = StateConfig(directory=tmp_path / "state")
    first, path = load_or_create_device_key(state)
    second, second_path = load_or_create_device_key(state)
    assert first == second
    assert path == second_path
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    path.unlink()
    target = tmp_path / "other"
    target.write_text("0" * 64, encoding="ascii")
    path.symlink_to(target)
    with pytest.raises(DeviceStateError, match="symlink"):
        load_or_create_device_key(state)


def test_control_protocol_rejects_unknown_privileged_fields() -> None:
    valid = {
        "commandId": "29fa15ec-54a1-4a2b-9562-5fef04b507be",
        "command": "forward",
        "timestamp": 123.0,
    }
    assert ControlCommand.model_validate(valid).command == "forward"
    with pytest.raises(ValidationError):
        ControlCommand.model_validate({**valid, "privileged": True})


def test_remote_action_schema_is_closed_and_scope_aware() -> None:
    action = RemoteAction.model_validate(
        {"type": "reset_safety", "actionId": "action-1", "scopes": ["SAFETY:RESET"]}
    )
    assert action.scopes == ["safety:reset"]
    with pytest.raises(ValidationError):
        RemoteAction.model_validate({"type": "shell", "command": "id"})
    with pytest.raises(ValidationError):
        RemoteAction.model_validate({"type": "restart_video", "durationSec": 30})


def test_ota_manifest_signature_and_bundle_path_validation(tmp_path) -> None:
    payload = {
        "version": "2.0.0",
        "bundleUrl": "https://releases.example/client.zip",
        "size": 123,
        "sha256": "a" * 64,
    }
    raw, public = _signed_manifest(payload)
    assert OtaManifest.parse(raw, public).version == "2.0.0"

    manager = object.__new__(UpdateManager)
    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside", b"bad")
    with pytest.raises(ArtifactVerificationError, match="unsafe path"):
        manager._extract_bundle(traversal.getvalue(), tmp_path / "traversal")

    symlink = io.BytesIO()
    with zipfile.ZipFile(symlink, "w") as archive:
        entry = zipfile.ZipInfo("wheelhouse/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "../../etc/passwd")
    with pytest.raises(ArtifactVerificationError, match="link or device"):
        manager._extract_bundle(symlink.getvalue(), tmp_path / "symlink")


def test_ota_boot_preparation_completes_activation_then_rolls_back(tmp_path) -> None:
    state = tmp_path / "ota"
    previous = state / "slots" / "a"
    target = state / "slots" / "b"
    for slot in (previous, target):
        python = slot / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o755)
    state.mkdir(exist_ok=True)
    (state / "current").symlink_to(previous)
    (state / "pending.json").write_text(
        json.dumps({"version": "2.0.0", "previous": str(previous), "target": str(target)}),
        encoding="utf-8",
    )

    assert prepare_boot(state) == target / "venv" / "bin" / "python"
    assert (state / "current").resolve() == target
    assert (state / "boot-attempted").is_file()

    assert prepare_boot(state) == previous / "venv" / "bin" / "python"
    assert (state / "current").resolve() == previous
    assert not (state / "pending.json").exists()
