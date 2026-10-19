from __future__ import annotations

import copy
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from botparty_robot.__main__ import ConfigLoadError, _load_config_from
from botparty_robot.backup import create_encrypted_backup, generate_backup_key
from botparty_robot.config_migration import (
    legacy_fields,
    migrate_config,
    migrate_config_file,
    reject_legacy_config,
)


def _legacy() -> dict:
    return {
        "server": {"claim_token": "private-claim", "device_key": "private-device"},
        "controls": {
            "gpio_enabled": True,
            "motor_left_forward": 17,
            "motor_left_backward": 18,
            "motor_right_forward": 22,
            "motor_right_backward": 23,
        },
        "camera": {"pipeline": "libcamera"},
        "tts": {
            "type": "espeak",
            "speaker_num": "1,0",
            "delay_tts": True,
            "delay": 1.5,
            "tts_volume": 55,
            "filter_url_tts": False,
            "anon_tts": True,
        },
        "safety": {"emergency_stop_pin": 24},
    }


def test_explicit_migration_preserves_effective_settings_and_secrets() -> None:
    raw = _legacy()
    original = copy.deepcopy(raw)
    result = migrate_config(raw)
    assert raw == original
    assert result["server"] == raw["server"]
    assert result["hardware"] == {
        "type": "l298n",
        "options": {
            "forward_pins": [17],
            "backward_pins": [18],
            "left_pins": [22],
            "right_pins": [23],
        },
    }
    assert result["video"]["type"] == "ffmpeg_libcamera"
    assert result["tts"]["playback_device"] == "plughw:1,0"
    assert result["tts"]["delay_ms"] == 1500
    assert result["tts"]["volume"] == 55
    assert result["tts"]["filter_urls"] is False
    assert result["tts"]["allow_anonymous"] is True
    assert result["tts"]["enabled"] is True
    assert result["safety"] == {}
    assert legacy_fields(result) == []
    assert migrate_config(result) == result


def test_modern_values_win_over_legacy_fields() -> None:
    raw = _legacy()
    raw["hardware"] = {"type": "none"}
    raw["video"] = {"type": "none"}
    raw["tts"].update(enabled=False, playback_device="modern", delay_ms=0, volume=20)
    result = migrate_config(raw)
    assert result["hardware"] == raw["hardware"]
    assert result["video"] == raw["video"]
    assert result["tts"]["enabled"] is False
    assert result["tts"]["playback_device"] == "modern"
    assert result["tts"]["delay_ms"] == 0
    assert result["tts"]["volume"] == 20


@pytest.mark.parametrize(
    "section,profile,expected",
    [
        ("hardware", "hardware_custom", "custom"),
        ("hardware", "hardware_custom_example", "custom"),
        ("video", "ffmpeg_tcp_livekit", "botparty_streamer"),
        ("video", "go_h264_publisher", "botparty_streamer"),
        ("video", "lk_h264_publisher", "botparty_streamer"),
    ],
)
def test_profile_aliases_require_explicit_migration(section, profile, expected) -> None:
    raw = {"server": {"claim_token": "test-claim-token"}, section: {"type": profile}}
    with pytest.raises(ValueError, match="config migrate"):
        reject_legacy_config(raw)
    assert migrate_config(raw)[section]["type"] == expected


@pytest.mark.parametrize(
    "tts,expected",
    [
        ({"speaker_device": "hw:2", "audio_device": "hw:3"}, "hw:2"),
        ({"audio_device": "hw:3"}, "hw:3"),
        ({"hw_num": "2,0"}, "plughw:2,0"),
        ({"speaker_num": ""}, "default"),
    ],
)
def test_tts_device_variants(tts, expected) -> None:
    result = migrate_config({"server": {"claim_token": "test-claim-token"}, "tts": tts})
    assert result["tts"].get("playback_device", "default") == expected


@pytest.mark.parametrize(
    "patch",
    [
        {"controls": "invalid"},
        {"controls": {"unrecognized_motor_setting": 1}},
        {"camera": {"pipeline": ""}},
        {"camera": {"pipeline": 1}},
        {"tts": {"delay_tts": True, "delay": "secret-invalid-delay"}},
        {"tts": {"delay_tts": True, "delay": float("inf")}},
        {"tts": {"delay_tts": True, "delay": float("nan")}},
        {"tts": {"tts_volume": 101}},
    ],
)
def test_invalid_legacy_settings_are_not_silently_replaced(patch) -> None:
    with pytest.raises((ValueError, ValidationError)):
        migrate_config({"server": {"claim_token": "test-claim-token"}, **patch})


def test_cli_migration_does_not_overwrite_source_or_print_credentials(tmp_path: Path) -> None:
    source, output = tmp_path / "old.yaml", tmp_path / "new.yaml"
    source.write_text(yaml.safe_dump(_legacy()), encoding="utf-8")
    source.chmod(0o600)
    before = source.read_bytes()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "botparty_robot",
            "--config",
            str(source),
            "config",
            "migrate",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "private-claim" not in result.stdout + result.stderr
    assert "private-device" not in result.stdout + result.stderr
    assert source.read_bytes() == before
    assert output.stat().st_mode & 0o777 == 0o600
    config = _load_config_from(str(output), persist_device_key=False)
    assert config.hardware.type == "l298n"
    assert config.server.claim_token_value() == "private-claim"
    with pytest.raises(FileExistsError):
        migrate_config_file(source, output)
    with pytest.raises(ValueError, match="differ"):
        migrate_config_file(source, source)
    with pytest.raises(ConfigLoadError, match="Legacy configuration"):
        _load_config_from(str(source), persist_device_key=False)


def test_migration_refuses_output_symlinks_and_invalid_inputs(tmp_path: Path) -> None:
    source, target, output = (tmp_path / name for name in ("old.yaml", "target", "output"))
    source.write_text(yaml.safe_dump(_legacy()), encoding="utf-8")
    source.chmod(0o600)
    target.write_text("untouched", encoding="utf-8")
    output.symlink_to(target)
    with pytest.raises(FileExistsError):
        migrate_config_file(source, output)
    assert target.read_text() == "untouched"
    source.write_text("- list", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML object"):
        migrate_config_file(source, tmp_path / "new")
    assert not (tmp_path / "new").exists()


def test_migration_removes_incomplete_output_on_write_failure(tmp_path: Path, monkeypatch) -> None:
    source, output = tmp_path / "old.yaml", tmp_path / "new.yaml"
    source.write_text(yaml.safe_dump(_legacy()), encoding="utf-8")
    source.chmod(0o600)

    def fail(_fd):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(OSError, match="disk failure"):
        migrate_config_file(source, output)
    assert not output.exists()


def test_disabled_controls_keep_safe_defaults() -> None:
    raw = {"server": {"claim_token": "test-claim-token"}, "controls": {"gpio_enabled": False}}
    result = migrate_config(raw)
    assert "controls" not in result
    assert "hardware" not in result


def test_nested_video_aliases_and_normalized_names_require_migration() -> None:
    raw = {
        "server": {"claim_token": "test-claim-token"},
        "hardware": {"type": " Hardware-Custom "},
        "cameras": [{"id": "front", "video": {"type": "GO-H264-PUBLISHER"}}],
    }
    assert legacy_fields(raw) == ["hardware.type", "cameras.0.video.type"]
    result = migrate_config(raw)
    assert result["hardware"]["type"] == "custom"
    assert result["cameras"][0]["video"]["type"] == "botparty_streamer"


def test_untyped_video_override_is_valid_without_legacy_fields() -> None:
    raw = {"server": {"claim_token": "test-claim-token"}, "cameras": [{"id": "front", "video": {}}]}
    assert migrate_config(raw) == raw


def test_legacy_backup_is_rejected_before_replacing_files(tmp_path: Path) -> None:
    source, key, device, archive, target = (
        tmp_path / name for name in ("old.yaml", "backup.key", "device", "state.bpb", "target")
    )
    source.write_text(yaml.safe_dump(_legacy()), encoding="utf-8")
    device.write_text("a" * 64 + "\n", encoding="ascii")
    source.chmod(0o600)
    device.chmod(0o600)
    generate_backup_key(key)
    create_encrypted_backup(
        config_path=source,
        device_key_path=device,
        output_path=archive,
        key_path=key,
    )
    target.write_text("preserve-current-configuration", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "botparty_robot",
            "--config",
            str(target),
            "backup",
            "restore",
            "--key-file",
            str(key),
            "--input",
            str(archive),
            "--state-dir",
            str(tmp_path / "state"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "previous client" in result.stderr
    assert target.read_text() == "preserve-current-configuration"
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize(
    "content",
    [
        "server: [",
        "server:\n  claim_token: private-claim\ntts:\n  volume: 999\n",
    ],
)
def test_cli_invalid_input_is_actionable_and_redacts_values(tmp_path: Path, content: str) -> None:
    source, output = tmp_path / "old.yaml", tmp_path / "new.yaml"
    source.write_text(content, encoding="utf-8")
    source.chmod(0o600)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "botparty_robot",
            "--config",
            str(source),
            "config",
            "migrate",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "private-claim" not in result.stdout + result.stderr
    assert not output.exists()
