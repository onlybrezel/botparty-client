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
    run_doctor,
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
    config.chmod(0o600)
    device_key.chmod(0o600)
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
    monkeypatch.setattr(
        "botparty_robot.operator.missing_hardware_dependency", lambda _profile: None
    )
    config = RobotConfig(
        server=ServerConfig(
            claim_token="claim-token-secret",
            robot_auth_token="runtime-token-secret",
        ),
        hardware={
            "type": "mqtt_pub",
            "options": {
                "password": "mqtt-password-secret",
            },
        },
        video={
            "type": "ffmpeg",
            "options": {"publisher_binary": "https://video-user:video-token-secret@example.com"},
        },
        tts={
            "enabled": True,
            "type": "custom",
            "options": {
                "neutral": "custom-neutral-secret",
                "nested": {"api_key": "nested-api-key-secret"},
            },
        },
    )
    exported = tmp_path / "export.yaml"
    target = tmp_path / "config.yaml"
    support = tmp_path / "support.zip"
    export_config(config, exported)
    export_text = exported.read_text(encoding="utf-8")
    for secret in (
        "claim-token-secret",
        "mqtt-password-secret",
        "nested-api-key-secret",
        "video-token-secret",
        "custom-neutral-secret",
    ):
        assert secret not in export_text
    assert "hardwareProfile" in export_text

    monkeypatch.setenv("BOTPARTY_CLAIM_TOKEN", "replacement-token")
    target.write_text(
        "server:\n  claim_token: old-claim-token-secret\n",  # secret-scan: allow-test-fixture
        encoding="utf-8",
    )
    raw, diff = import_config_preview(exported, target)
    assert raw["server"]["claim_token"] == "replacement-token"
    assert "replacement-token" not in diff
    assert "old-claim-token-secret" not in diff
    assert "${BOTPARTY_CLAIM_TOKEN}" in diff

    write_support_bundle(config, support)
    with zipfile.ZipFile(support) as archive:
        content = b"\n".join(archive.read(name) for name in archive.namelist())
    assert b"claim-token-secret" not in content
    assert b"runtime-token-secret" not in content
    assert b"mqtt-password-secret" not in content
    assert b"nested-api-key-secret" not in content
    assert b"video-token-secret" not in content
    assert b"custom-neutral-secret" not in content


def test_restore_rejects_a_different_initialized_device(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    device_key = tmp_path / "device-key"
    backup_key = tmp_path / "backup.key"
    backup = tmp_path / "state.bpb"
    config.write_text("server:\n  claim_token: secret-token\n", encoding="utf-8")
    device_key.write_text("a" * 64 + "\n", encoding="ascii")
    config.chmod(0o600)
    device_key.chmod(0o600)
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
    answers = iter(
        (
            "ffmpeg",
            "/dev/video0",
            "https://botparty.live",
            "wss://botparty.live",
            "PASTE_YOUR_CLAIM_TOKEN_HERE",
        )
    )

    create_setup_config(output, input_fn=lambda _prompt: next(answers))

    assert (tmp_path / "config.yaml.before-import").read_text(encoding="utf-8") == "old: value\n"
    created = output.read_text(encoding="utf-8")
    assert "PASTE_YOUR_CLAIM_TOKEN_HERE" in created
    assert "releases/latest/download/ota-manifest.json" in created
    assert "enabled: false" in created
    assert output.stat().st_mode & 0o777 == 0o600


def test_setup_abort_does_not_change_the_target(tmp_path) -> None:
    output = tmp_path / "config.yaml"
    output.write_text("old: value\n", encoding="utf-8")

    with pytest.raises(KeyboardInterrupt):
        create_setup_config(
            output, input_fn=lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt)
        )

    assert output.read_text(encoding="utf-8") == "old: value\n"


def test_doctor_keeps_stable_check_order_for_a_minimal_host(tmp_path, monkeypatch) -> None:
    state_directory = tmp_path / "state"
    state_directory.mkdir(mode=0o700)
    monkeypatch.setattr("botparty_robot.operator.os.geteuid", lambda: state_directory.stat().st_uid)

    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        state={"directory": state_directory},
        hardware={"type": "none"},
        video={"type": "none"},
    )

    checks = run_doctor(config)

    assert [check.name for check in checks] == [
        "python",
        "platform",
        "service_user",
        "hardware_release_status",
        "state_directory",
    ]
    assert [check.status for check in checks] == ["OK", "OK", "OK", "OK", "OK"]
    assert checks[3].detail == "supported; motionCommands=0; productionMotion=enabled"
    assert checks[4].detail == str(state_directory)
