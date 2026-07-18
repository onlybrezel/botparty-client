from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import botparty_robot.ota as ota_module
from botparty_robot.artifacts import ArtifactVerificationError, normalized_arch
from botparty_robot.config import OtaConfig
from botparty_robot.ota import (
    MAX_OTA_MANIFEST_BYTES,
    OtaManifest,
    UpdateManager,
    _download,
    prepare_boot,
)


def _bundle() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "installer-requirements.txt", "pip==26.1.2 --hash=sha256:" + "1" * 64 + "\n"
        )
        archive.writestr("requirements.txt", "package==1 --hash=sha256:" + "0" * 64 + "\n")
        archive.writestr("wheelhouse/package-1-py3-none-any.whl", b"wheel")
    return output.getvalue()


def _sign_payload(payload: dict[str, object]) -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = dict(payload)
    envelope["signature"] = base64.b64encode(private.sign(signed)).decode()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return json.dumps(envelope).encode(), public


def _manifest_payload(bundle: bytes, version: str = "2.0.0") -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "version": version,
        "platform": "linux",
        "arch": normalized_arch(),
        "bundleUrl": "https://releases.example/client.zip",
        "size": len(bundle),
        "sha256": hashlib.sha256(bundle).hexdigest(),
    }


def _signed_manifest(bundle: bytes, version: str = "2.0.0") -> tuple[bytes, bytes]:
    return _sign_payload(_manifest_payload(bundle, version))


def _manager(tmp_path: Path, public: bytes) -> tuple[UpdateManager, Path]:
    state = tmp_path / "ota"
    state.mkdir(mode=0o700)
    state.chmod(0o700)
    key = tmp_path / "release.pub"
    key.write_text(base64.b64encode(public).decode(), encoding="ascii")
    key.chmod(0o600)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("server: {}\n", encoding="utf-8")
    config_path.chmod(0o600)
    manager = UpdateManager(
        OtaConfig(
            enabled=True,
            manifest_url="https://releases.example/manifest.json",
            public_key_file=key,
            state_directory=state,
        )
    )
    return manager, config_path


def _fake_subprocess_run(command: list[str], **kwargs: object) -> SimpleNamespace:
    del kwargs
    if command[1:3] == ["-m", "venv"]:
        python = Path(command[3]) / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
    if command[1:4] == ["-m", "pip", "uninstall"]:
        (Path(command[0]).parent / "pip").unlink(missing_ok=True)
        (Path(command[0]).parent / "pip3").unlink(missing_ok=True)
    if command[-1] == "--version":
        return SimpleNamespace(returncode=0, stdout="botparty-robot 2.0.0\n", stderr="")
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_install_confirm_and_action_identity_form_one_transaction(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))
    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", _fake_subprocess_run)

    executable = manager.install(config_path, action_id="update-action-1")
    assert executable.is_file()
    assert manager.current_link.resolve() == executable.parents[2]
    assert manager.boot_attempted_file.read_text(encoding="ascii") == "1\n"
    pending = json.loads(manager.pending_file.read_text(encoding="utf-8"))
    assert pending["actionId"] == "update-action-1"

    assert manager.confirm(executable) == "update-action-1"
    assert not manager.pending_file.exists()
    assert not manager.boot_attempted_file.exists()


def test_failed_install_never_switches_current_slot_and_cleans_staging(
    tmp_path, monkeypatch
) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    previous = manager.slots_directory / "b"
    previous.mkdir(parents=True)
    manager.current_link.symlink_to(previous)
    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))

    calls = 0

    def fail_pip(command: list[str], **kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if command[1:3] == ["-m", "venv"]:
            return _fake_subprocess_run(command, **kwargs)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", fail_pip)
    with pytest.raises(subprocess.CalledProcessError):
        manager.install(config_path)

    assert calls == 2
    assert manager.current_link.resolve() == previous.resolve()
    assert not manager.pending_file.exists()
    assert not manager.boot_attempted_file.exists()
    assert not any(manager.slots_directory.glob(".*.staging"))


@pytest.mark.parametrize("mode", [0o755, 0o770])
def test_ota_state_and_markers_reject_non_private_permissions(tmp_path, mode) -> None:
    bundle = _bundle()
    _, public = _signed_manifest(bundle)
    manager, _ = _manager(tmp_path, public)
    manager.state_directory.chmod(mode)
    with pytest.raises(ArtifactVerificationError, match="mode 0700"):
        manager.rollback_if_unconfirmed()


def test_confirm_rejects_wrong_interpreter_and_tampered_release(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))
    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", _fake_subprocess_run)
    executable = manager.install(config_path)

    wrong = tmp_path / "wrong-python"
    wrong.write_text("#!/bin/sh\n", encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="pending slot"):
        manager.confirm(wrong)

    release = executable.parents[2] / "release.json"
    payload = json.loads(release.read_text(encoding="utf-8"))
    payload["version"] = "tampered"
    release.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="identity"):
        manager.confirm(executable)


def test_rollback_without_previous_slot_clears_activation(tmp_path) -> None:
    bundle = _bundle()
    _, public = _signed_manifest(bundle)
    manager, _ = _manager(tmp_path, public)
    target = manager.slots_directory / "a"
    target.mkdir(parents=True)
    manager.current_link.symlink_to(target)
    manager.pending_file.write_text(
        json.dumps({"previous": None, "target": str(target)}), encoding="utf-8"
    )
    manager.pending_file.chmod(0o600)
    manager.boot_attempted_file.write_text("1\n", encoding="ascii")
    manager.boot_attempted_file.chmod(0o600)

    assert manager.rollback_current_update() is True
    assert not manager.current_link.exists()
    assert manager.rollback_current_update() is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schemaVersion", 1, "schema"),
        ("version", "bad/version", "version"),
        ("platform", "windows", "platform"),
        ("arch", "unsupported", "architecture"),
        ("bundleUrl", "http://releases.example/client.zip", "HTTPS"),
        ("bundleUrl", "https://user:pass@releases.example/client.zip", "HTTPS"),
        ("size", 0, "size"),
        ("size", 513 * 1024 * 1024, "size"),
        ("sha256", "not-a-digest", "SHA-256"),
    ],
)
def test_manifest_rejects_every_invalid_trust_field(field, value, message) -> None:
    bundle = _bundle()
    payload = _manifest_payload(bundle)
    payload[field] = value
    raw, public = _sign_payload(payload)
    with pytest.raises(ArtifactVerificationError, match=message):
        OtaManifest.parse(raw, public)


def test_manifest_rejects_shape_size_and_signature_failures() -> None:
    bundle = _bundle()
    raw, public = _signed_manifest(bundle)
    with pytest.raises(ArtifactVerificationError, match="64 KiB"):
        OtaManifest.parse(b" " * (MAX_OTA_MANIFEST_BYTES + 1), public)
    with pytest.raises(ArtifactVerificationError, match="valid JSON"):
        OtaManifest.parse(b"{", public)
    with pytest.raises(ArtifactVerificationError, match="object"):
        OtaManifest.parse(b"[]", public)
    with pytest.raises(ArtifactVerificationError, match="signature"):
        OtaManifest.parse(raw.replace(b'"signature": "', b'"signature": "broken'), public)

    payload = _manifest_payload(bundle)
    payload["unexpected"] = True
    wrong_fields, wrong_fields_public = _sign_payload(payload)
    with pytest.raises(ArtifactVerificationError, match="fields"):
        OtaManifest.parse(wrong_fields, wrong_fields_public)


class _DownloadResponse:
    def __init__(self, data: bytes, final_url: str) -> None:
        self.data = data
        self.final_url = final_url

    def __enter__(self) -> _DownloadResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self.final_url

    def read(self, _limit: int) -> bytes:
        return self.data


def test_download_enforces_initial_redirect_and_response_size(monkeypatch) -> None:
    with pytest.raises(ArtifactVerificationError, match="HTTPS"):
        _download("http://releases.example/file", 4, 1)

    monkeypatch.setattr(
        "botparty_robot.ota.urllib.request.urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(b"ok", "http://redirect.example/file"),
    )
    with pytest.raises(ArtifactVerificationError, match="redirect"):
        _download("https://releases.example/file", 4, 1)

    monkeypatch.setattr(
        "botparty_robot.ota.urllib.request.urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(b"12345", "https://releases.example/file"),
    )
    with pytest.raises(ArtifactVerificationError, match="exceeds"):
        _download("https://releases.example/file", 4, 1)

    monkeypatch.setattr(
        "botparty_robot.ota.urllib.request.urlopen",
        lambda *_args, **_kwargs: _DownloadResponse(b"1234", "https://releases.example/file"),
    )
    assert _download("https://releases.example/file", 4, 1) == b"1234"


def test_install_rejects_symlink_config_before_network_access(tmp_path) -> None:
    bundle = _bundle()
    _, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    config_path.unlink()
    real_config = tmp_path / "real-config.yaml"
    real_config.write_text("server: {}\n", encoding="utf-8")
    config_path.symlink_to(real_config)
    with pytest.raises(ArtifactVerificationError, match="non-symlink"):
        manager.install(config_path)


@pytest.mark.parametrize("failure", ["length", "digest", "layout", "version"])
def test_install_faults_leave_no_pending_activation(tmp_path, monkeypatch, failure) -> None:
    bundle = _bundle()
    payload = _manifest_payload(bundle)
    delivered = bundle
    if failure == "length":
        payload["size"] = len(bundle) + 1
    elif failure == "digest":
        payload["sha256"] = "0" * 64
    elif failure == "layout":
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("README.txt", "missing offline wheelhouse")
        delivered = stream.getvalue()
        payload = _manifest_payload(delivered)
    manifest, public = _sign_payload(payload)
    manager, config_path = _manager(tmp_path, public)
    downloads = iter((manifest, delivered))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))

    if failure == "version":

        def wrong_version(command: list[str], **kwargs: object) -> SimpleNamespace:
            result = _fake_subprocess_run(command, **kwargs)
            if command[-1] == "--version":
                result.stdout = "botparty-robot 9.9.9\n"
            return result

        monkeypatch.setattr("botparty_robot.ota.run_sandboxed", wrong_version)
    else:
        monkeypatch.setattr("botparty_robot.ota.run_sandboxed", _fake_subprocess_run)

    expected = {
        "length": "length",
        "digest": "SHA-256",
        "layout": "wheelhouse",
        "version": "version",
    }[failure]
    with pytest.raises(ArtifactVerificationError, match=expected):
        manager.install(config_path)
    assert not manager.pending_file.exists()
    assert not manager.boot_attempted_file.exists()


def test_state_validation_rejects_pointer_marker_and_metadata_shapes(tmp_path) -> None:
    bundle = _bundle()
    _, public = _signed_manifest(bundle)
    manager, _ = _manager(tmp_path, public)
    manager.current_link.write_text("not-a-symlink", encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="pointer"):
        manager._validate_state_directory(create=False)

    manager.current_link.unlink()
    manager.pending_file.write_text("{}", encoding="utf-8")
    manager.pending_file.chmod(0o644)
    with pytest.raises(ArtifactVerificationError, match="marker"):
        manager._validate_state_directory(create=False)

    manager.pending_file.write_text("[]", encoding="utf-8")
    manager.pending_file.chmod(0o600)
    with pytest.raises(ArtifactVerificationError, match="object"):
        manager._load_pending()

    manager.pending_file.unlink()
    manager.pending_file.symlink_to(tmp_path / "missing")
    with pytest.raises(ArtifactVerificationError, match="regular file"):
        manager._load_pending()


def test_bundle_extractor_rejects_non_zip_payload(tmp_path) -> None:
    manager = object.__new__(UpdateManager)
    with pytest.raises(ArtifactVerificationError, match="ZIP"):
        manager._extract_bundle(b"not-a-zip", tmp_path / "extract")


def test_crash_before_ready_rolls_back_once_and_confirmed_boot_stays_current(tmp_path) -> None:
    bundle = _bundle()
    _, public = _signed_manifest(bundle)
    manager, _ = _manager(tmp_path, public)
    previous = manager.slots_directory / "a"
    target = manager.slots_directory / "b"
    for slot in (previous, target):
        python = slot / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n", encoding="utf-8")
        python.chmod(0o700)
    manager.current_link.symlink_to(previous)
    manager.pending_file.write_text(
        json.dumps(
            {
                "version": "2.0.0",
                "sha256": "a" * 64,
                "previous": str(previous),
                "target": str(target),
                "executable": str(target / "venv" / "bin" / "python"),
                "actionId": "crash-action",
            }
        ),
        encoding="utf-8",
    )
    manager.pending_file.chmod(0o600)

    assert prepare_boot(manager.state_directory) == target / "venv" / "bin" / "python"
    assert manager.boot_attempted_file.is_file()
    assert manager.current_link.resolve() == target.resolve()

    assert prepare_boot(manager.state_directory) == previous / "venv" / "bin" / "python"
    assert manager.current_link.resolve() == previous.resolve()
    assert not manager.pending_file.exists()
    assert not manager.boot_attempted_file.exists()

    manager.current_link.unlink()
    manager.current_link.symlink_to(target)
    assert prepare_boot(manager.state_directory) == target / "venv" / "bin" / "python"
    assert manager.current_link.resolve() == target.resolve()


def test_disk_full_before_activation_preserves_previous_slot(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    previous = manager.slots_directory / "b"
    previous.mkdir(parents=True)
    manager.current_link.symlink_to(previous)
    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))
    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", _fake_subprocess_run)
    real_atomic_text = ota_module._atomic_text

    def fail_pending(path: Path, value: str, encoding: str) -> None:
        if path.name == "pending.json":
            raise OSError(28, "No space left on device")
        real_atomic_text(path, value, encoding)

    monkeypatch.setattr(ota_module, "_atomic_text", fail_pending)
    with pytest.raises(OSError, match="No space"):
        manager.install(config_path)

    assert manager.current_link.resolve() == previous.resolve()
    assert not manager.pending_file.exists()
    assert not manager.boot_attempted_file.exists()


def test_network_abort_and_config_validation_failure_never_activate(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)

    monkeypatch.setattr(
        "botparty_robot.ota._download",
        lambda *_args: (_ for _ in ()).throw(TimeoutError("network interrupted")),
    )
    with pytest.raises(TimeoutError, match="network interrupted"):
        manager.install(config_path)
    assert not manager.current_link.exists()

    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))

    def fail_config(command: list[str], **kwargs: object) -> SimpleNamespace:
        if command[-2:] == ["config", "validate"]:
            raise subprocess.CalledProcessError(2, command)
        return _fake_subprocess_run(command, **kwargs)

    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", fail_config)
    with pytest.raises(subprocess.CalledProcessError):
        manager.install(config_path)
    assert not manager.current_link.exists()
    assert not manager.pending_file.exists()


def test_runtime_pip_must_be_removed_before_ota_activation(tmp_path, monkeypatch) -> None:
    bundle = _bundle()
    manifest, public = _signed_manifest(bundle)
    manager, config_path = _manager(tmp_path, public)
    downloads = iter((manifest, bundle))
    monkeypatch.setattr("botparty_robot.ota._download", lambda *_args: next(downloads))

    def leave_pip(command: list[str], **kwargs: object) -> SimpleNamespace:
        result = _fake_subprocess_run(command, **kwargs)
        if command[1:4] == ["-m", "pip", "uninstall"]:
            pip = Path(command[0]).parent / "pip"
            pip.write_text("#!/bin/sh\n", encoding="utf-8")
        return result

    monkeypatch.setattr("botparty_robot.ota.run_sandboxed", leave_pip)
    with pytest.raises(ArtifactVerificationError, match="pip removal"):
        manager.install(config_path)
    assert not manager.current_link.exists()
    assert not manager.pending_file.exists()
