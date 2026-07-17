import zipfile

import pytest

from botparty_robot.backup import (
    BackupError,
    create_encrypted_backup,
    generate_backup_key,
    read_encrypted_backup,
    restore_encrypted_backup,
)
from botparty_robot.config import RobotConfig, ServerConfig
from botparty_robot.operator import (
    create_setup_config,
    export_config,
    import_config_preview,
    write_support_bundle,
)


def test_encrypted_backup_round_trip_and_wrong_key(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    device_key = tmp_path / "device-key"
    key = tmp_path / "backup.key"
    other_key = tmp_path / "other.key"
    backup = tmp_path / "state.bpb"
    config.write_text("server:\n  claim_token: secret-token\n", encoding="utf-8")
    device_key.write_text("a" * 64 + "\n", encoding="ascii")
    generate_backup_key(key)
    generate_backup_key(other_key)

    create_encrypted_backup(
        config_path=config,
        device_key_path=device_key,
        output_path=backup,
        key_path=key,
    )
    assert b"secret-token" not in backup.read_bytes()
    assert read_encrypted_backup(backup, key)["device-key"].startswith(b"a")
    with pytest.raises(BackupError, match="authentication"):
        read_encrypted_backup(backup, other_key)

    config.write_text("broken", encoding="utf-8")
    restore_encrypted_backup(
        input_path=backup,
        key_path=key,
        config_path=config,
        device_key_path=device_key,
    )
    assert "secret-token" in config.read_text(encoding="utf-8")


def test_config_export_import_preview_and_support_bundle_are_redacted(
    tmp_path, monkeypatch
) -> None:
    config = RobotConfig(
        server=ServerConfig(
            claim_token="claim-token-secret",
            robot_auth_token="runtime-token-secret",
        )
    )
    exported = tmp_path / "export.yaml"
    target = tmp_path / "config.yaml"
    support = tmp_path / "support.zip"
    export_config(config, exported)
    assert "claim-token-secret" not in exported.read_text(encoding="utf-8")
    assert "hardwareProfile" in exported.read_text(encoding="utf-8")

    monkeypatch.setenv("BOTPARTY_CLAIM_TOKEN", "replacement-token")
    raw, diff = import_config_preview(exported, target)
    assert raw["server"]["claim_token"] == "replacement-token"
    assert "replacement-token" in diff

    write_support_bundle(config, support)
    with zipfile.ZipFile(support) as archive:
        content = b"\n".join(archive.read(name) for name in archive.namelist())
    assert b"claim-token-secret" not in content
    assert b"runtime-token-secret" not in content


def test_restore_rejects_a_different_initialized_device(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    device_key = tmp_path / "device-key"
    backup_key = tmp_path / "backup.key"
    backup = tmp_path / "state.bpb"
    config.write_text("server:\n  claim_token: secret-token\n", encoding="utf-8")
    device_key.write_text("a" * 64 + "\n", encoding="ascii")
    generate_backup_key(backup_key)
    create_encrypted_backup(
        config_path=config,
        device_key_path=device_key,
        output_path=backup,
        key_path=backup_key,
    )
    device_key.write_text("b" * 64 + "\n", encoding="ascii")

    with pytest.raises(BackupError, match="different initialized device"):
        restore_encrypted_backup(
            input_path=backup,
            key_path=backup_key,
            config_path=config,
            device_key_path=device_key,
        )


def test_setup_is_atomic_and_preserves_the_previous_config(tmp_path) -> None:
    output = tmp_path / "config.yaml"
    output.write_text("old: value\n", encoding="utf-8")
    answers = iter(("none", "ffmpeg", "https://botparty.live", "wss://botparty.live"))

    create_setup_config(output, input_fn=lambda _prompt: next(answers))

    assert (tmp_path / "config.yaml.before-import").read_text(encoding="utf-8") == "old: value\n"
    created = output.read_text(encoding="utf-8")
    assert "PASTE_YOUR_CLAIM_TOKEN_HERE" in created
    assert output.stat().st_mode & 0o777 == 0o600


def test_setup_abort_does_not_change_the_target(tmp_path) -> None:
    output = tmp_path / "config.yaml"
    output.write_text("old: value\n", encoding="utf-8")

    with pytest.raises(KeyboardInterrupt):
        create_setup_config(
            output, input_fn=lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt)
        )

    assert output.read_text(encoding="utf-8") == "old: value\n"
