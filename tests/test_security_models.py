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
    verify_installed_streamer,
)
from botparty_robot.config import ServerConfig, StateConfig
from botparty_robot.device_state import (
    DeviceStateError,
    load_or_create_device_key,
    validate_trusted_code_file,
)
from botparty_robot.ota import OtaManifest, UpdateManager, prepare_boot
from botparty_robot.ota import main as ota_main
from botparty_robot.protocol import (
    ClaimRequest,
    ClaimResponse,
    ControlCommand,
    RemoteAction,
    validate_bounded_json,
    validate_command_value,
)
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


def test_custom_code_validation_rejects_writable_files_and_symlinks(tmp_path) -> None:
    source = tmp_path / "custom.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    source.chmod(0o664)
    with pytest.raises(DeviceStateError, match="not group/world writable"):
        validate_trusted_code_file(source, owner_uid=source.stat().st_uid)

    source.chmod(0o600)
    link = tmp_path / "custom-link.py"
    link.symlink_to(source)
    with pytest.raises(DeviceStateError, match="non-symlink"):
        validate_trusted_code_file(link, owner_uid=source.stat().st_uid)


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
    public_key.chmod(0o600)
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
    public_key.chmod(0o600)
    install_dir = tmp_path / "bin"
    downloads = iter((raw, binary))
    monkeypatch.setattr(
        "botparty_robot.artifacts._read_url", lambda *_args, **_kwargs: next(downloads)
    )
    monkeypatch.setattr(
        "botparty_robot.artifacts.run_sandboxed",
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
        "botparty_robot.artifacts.run_sandboxed",
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


def test_streamer_is_reverified_before_each_execution(tmp_path) -> None:
    binary = tmp_path / "botparty-streamer"
    binary.write_bytes(Path("/bin/true").read_bytes())
    binary.chmod(0o700)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    (tmp_path / "botparty-streamer.sha256").write_text(digest + "\n", encoding="ascii")
    (tmp_path / "botparty-streamer.sha256").chmod(0o600)

    assert verify_installed_streamer(binary) == digest
    binary.write_bytes(Path("/bin/false").read_bytes())
    with pytest.raises(ArtifactVerificationError, match="verification failed"):
        verify_installed_streamer(binary)


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


@pytest.mark.parametrize(
    "value",
    [
        {"nested": {"again": {"more": {"depth": {"six": {"seven": {"eight": 1}}}}}}},
        {f"key-{index}": index for index in range(65)},
        [0] * 129,
        "x" * 4097,
        float("inf"),
        2**53 + 1,
    ],
)
def test_command_payload_rejects_unbounded_or_non_interoperable_json(value) -> None:
    with pytest.raises(ValidationError):
        ControlCommand.model_validate(
            {
                "commandId": "command-1",
                "command": "test",
                "timestamp": 123.0,
                "value": value,
            }
        )


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ([list(range(4)) for _ in range(128)], "too many nodes"),
        ({"": 1}, "key is invalid"),
        ({1: "value"}, "key is invalid"),
        ({"x" * 129: 1}, "key is invalid"),
        (("not", "json"), "not JSON-compatible"),
    ],
)
def test_bounded_json_rejects_node_key_and_type_limits(value, message) -> None:
    with pytest.raises(ValueError, match=message):
        validate_bounded_json(value)


def test_bounded_json_accepts_all_interoperable_scalar_and_container_types() -> None:
    value = {"none": None, "bool": True, "int": 2**53, "float": 1.5, "items": ["ok"]}
    assert validate_bounded_json(value) is value


def test_command_value_schemas_are_closed_before_profile_dispatch() -> None:
    assert validate_command_value("forward", None, is_motion=True) is None
    assert validate_command_value("forward", 100, is_motion=True) == 100
    assert validate_command_value("left", {"x": -1, "y": 0.5}, is_motion=True) == {
        "x": -1,
        "y": 0.5,
    }
    assert validate_command_value("tts:say", {"text": "hello", "sender": "viewer"}) == {
        "text": "hello",
        "sender": "viewer",
    }
    assert validate_command_value("say", "hello") == "hello"
    assert validate_command_value("tts:volume", {"level": 75}) == {"level": 75}
    assert validate_command_value("tts.volume", 0) == 0
    with pytest.raises(ValueError, match="must not be boolean"):
        validate_command_value("forward", True, is_motion=True)
    with pytest.raises(ValueError, match="motion value"):
        validate_command_value("forward", 101, is_motion=True)
    with pytest.raises(ValueError, match="motion value"):
        validate_command_value("forward", {"x": 2, "y": 0}, is_motion=True)
    with pytest.raises(ValueError, match="unknown fields"):
        validate_command_value("tts:say", {"text": "hello", "nested": {}})
    with pytest.raises(ValueError, match="text must be a string"):
        validate_command_value("tts:say", {"text": 7})
    with pytest.raises(ValueError, match="anonymous flag"):
        validate_command_value("tts:say", {"text": "hello", "anonymous": "yes"})
    with pytest.raises(ValueError, match="identity fields"):
        validate_command_value("tts:say", {"text": "hello", "sender": 7})
    with pytest.raises(ValueError, match="closed speech object"):
        validate_command_value("tts:say", ["hello"])
    with pytest.raises(ValueError, match="volume must be numeric"):
        validate_command_value("tts:volume", True)
    with pytest.raises(ValueError, match="volume must be numeric"):
        validate_command_value("tts:volume", "loud")
    with pytest.raises(ValueError, match="between 0 and 100"):
        validate_command_value("tts:volume", 101)
    with pytest.raises(ValueError, match="do not accept"):
        validate_command_value("led_on", 1, hardware_command=True)


def test_claim_request_requires_hex_device_key_and_bounded_capabilities() -> None:
    request = ClaimRequest(
        claim_token="claim-token",
        device_key="d" * 64,
        capabilities={"hardware": "none"},
    )
    assert request.model_dump(by_alias=True)["deviceKey"] == "d" * 64
    with pytest.raises(ValidationError):
        ClaimRequest(claim_token="claim-token", device_key="z" * 64)
    with pytest.raises(ValidationError):
        ClaimRequest(
            claim_token="claim-token",
            device_key="d" * 64,
            capabilities={"payload": "x" * 4097},
        )
    with pytest.raises(ValidationError, match="must not be blank"):
        ClaimRequest(claim_token="   ", device_key="d" * 64)
    with pytest.raises(ValidationError, match="publish camera id is invalid"):
        ClaimRequest(claim_token="claim-token", device_key="d" * 64, publish_camera_ids=[" "])
    with pytest.raises(ValidationError, match="must be unique"):
        ClaimRequest(
            claim_token="claim-token",
            device_key="d" * 64,
            publish_camera_ids=["camera", "camera"],
        )


def test_remote_action_schema_is_closed_and_scope_aware() -> None:
    action = RemoteAction.model_validate(
        {"type": "reset_safety", "actionId": "action-1", "scopes": ["SAFETY:RESET"]}
    )
    assert action.scopes == ["safety:reset"]
    with pytest.raises(ValidationError):
        RemoteAction.model_validate({"type": "shell", "command": "id"})
    with pytest.raises(ValidationError):
        RemoteAction.model_validate({"type": "restart_video", "durationSec": 30})


def test_claim_protocol_bounds_publish_tokens_and_version() -> None:
    base = {
        "token": "token",
        "robotId": "robot-1",
        "livekitUrl": "wss://botparty.live",
        "robotAuthToken": "robot-auth",
    }
    with pytest.raises(ValidationError, match="at most eight"):
        ClaimResponse.model_validate(
            {**base, "publishTokens": {f"camera-{index}": "token" for index in range(9)}}
        )
    with pytest.raises(ValidationError, match="invalid camera id"):
        ClaimResponse.model_validate({**base, "publishTokens": {" ": "token"}})
    with pytest.raises(ValidationError, match="unsupported protocol version"):
        ClaimResponse.model_validate({**base, "protocolVersion": 2})


def test_remote_action_scope_validation_rejects_blank_and_duplicates() -> None:
    with pytest.raises(ValidationError, match="non-empty strings"):
        RemoteAction.model_validate({"actionId": "one", "type": "restart_tts", "scopes": [" "]})
    with pytest.raises(ValidationError, match="unique"):
        RemoteAction.model_validate(
            {"actionId": "two", "type": "restart_tts", "scopes": ["Speak:X", "speak:x"]}
        )


def test_ota_manifest_signature_and_bundle_path_validation(tmp_path) -> None:
    payload = {
        "schemaVersion": 2,
        "version": "2.0.0",
        "platform": "linux",
        "arch": normalized_arch(),
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
    state.mkdir(mode=0o700, exist_ok=True)
    state.chmod(0o700)
    (state / "current").symlink_to(previous)
    (state / "pending.json").write_text(
        json.dumps({"version": "2.0.0", "previous": str(previous), "target": str(target)}),
        encoding="utf-8",
    )
    (state / "pending.json").chmod(0o600)

    assert prepare_boot(state) == target / "venv" / "bin" / "python"
    assert (state / "current").resolve() == target
    assert (state / "boot-attempted").is_file()

    assert prepare_boot(state) == previous / "venv" / "bin" / "python"
    assert (state / "current").resolve() == previous
    assert not (state / "pending.json").exists()


def test_ota_cli_distinguishes_empty_invalid_and_rolled_back_state(tmp_path, capsys) -> None:
    state = tmp_path / "ota"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    assert ota_main(["prepare", "--state", str(state)]) == 3

    (state / "pending.json").write_text("{}", encoding="utf-8")
    (state / "pending.json").chmod(0o600)
    with pytest.raises(SystemExit) as invalid:
        ota_main(["prepare", "--state", str(state)])
    assert invalid.value.code == 1
    assert "OTA boot rejected" in capsys.readouterr().err


def test_ota_rollback_rejects_previous_slot_outside_state(tmp_path) -> None:
    state = tmp_path / "ota"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    outside = tmp_path / "outside"
    outside.mkdir()
    (state / "pending.json").write_text(
        json.dumps({"previous": str(outside), "target": str(outside)}), encoding="utf-8"
    )
    (state / "pending.json").chmod(0o600)
    (state / "boot-attempted").write_text("1\n", encoding="ascii")
    (state / "boot-attempted").chmod(0o600)

    with pytest.raises(SystemExit) as invalid:
        ota_main(["prepare", "--state", str(state)])
    assert invalid.value.code == 1
