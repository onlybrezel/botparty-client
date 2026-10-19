"""Explicit, offline conversion of retired configuration formats."""

from __future__ import annotations

import copy
import math
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from .config import RobotConfig
from .device_state import read_configuration_file

TTS_KEYS = {
    "speaker_device",
    "audio_device",
    "speaker_num",
    "hw_num",
    "delay_tts",
    "delay",
    "tts_volume",
    "filter_url_tts",
    "anon_tts",
}
PROFILE_ALIASES = {
    "hardware": {"hardware_custom": "custom", "hardware_custom_example": "custom"},
    "video": {
        "ffmpeg_tcp_livekit": "botparty_streamer",
        "go_h264_publisher": "botparty_streamer",
        "lk_h264_publisher": "botparty_streamer",
    },
}


def _profile_blocks(raw: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any], dict[str, str]]]:
    for section, aliases in PROFILE_ALIASES.items():
        block = raw.get(section)
        if isinstance(block, dict):
            yield section, block, aliases
    cameras = raw.get("cameras")
    if isinstance(cameras, list):
        for index, camera in enumerate(cameras):
            if isinstance(camera, dict) and isinstance(camera.get("video"), dict):
                yield f"cameras.{index}.video", camera["video"], PROFILE_ALIASES["video"]


def _normalized_profile(block: dict[str, Any]) -> str:
    profile = block.get("type")
    return (
        profile.strip().lower().replace("-", "_").replace("/", "_")
        if isinstance(profile, str)
        else ""
    )


def legacy_fields(raw: dict[str, Any]) -> list[str]:
    fields = ["controls"] if "controls" in raw else []
    camera, tts, safety = (raw.get(name) for name in ("camera", "tts", "safety"))
    if isinstance(camera, dict) and "pipeline" in camera:
        fields.append("camera.pipeline")
    if "tts" in raw and tts is None:
        fields.append("tts (null)")
    if isinstance(tts, dict):
        fields.extend(f"tts.{key}" for key in sorted(TTS_KEYS & tts.keys()))
        if "enabled" not in tts and tts.get("type", "none") != "none":
            fields.append("tts.enabled (implicit)")
    if isinstance(safety, dict) and "emergency_stop_pin" in safety:
        fields.append("safety.emergency_stop_pin")
    for section, block, aliases in _profile_blocks(raw):
        if _normalized_profile(block) in aliases:
            fields.append(f"{section}.type")
    return fields


def reject_legacy_config(raw: dict[str, Any]) -> None:
    fields = legacy_fields(raw)
    if fields:
        raise ValueError(
            "Legacy configuration is no longer supported: "
            + ", ".join(fields)
            + ". Run 'botparty-robot --config OLD.yaml config migrate --output NEW.yaml', "
            "review the result and validate it before replacing the original. "
            "For a legacy backup, restore with the previous client and migrate before upgrading."
        )


def migrate_config(raw: dict[str, Any]) -> dict[str, Any]:
    """Preserve the previous loader's effective pin mapping and explicit modern values."""
    raw = copy.deepcopy(raw)
    controls = raw.pop("controls", None)
    if controls is not None:
        allowed = {
            "gpio_enabled",
            "motor_left_forward",
            "motor_left_backward",
            "motor_right_forward",
            "motor_right_backward",
        }
        if not isinstance(controls, dict) or controls.keys() - allowed:
            raise ValueError("controls contains unsupported fields; migrate them manually")
        if "hardware" not in raw and controls.get("gpio_enabled"):
            raw["hardware"] = {
                "type": "l298n",
                "options": {
                    "forward_pins": [controls.get("motor_left_forward")],
                    "backward_pins": [controls.get("motor_left_backward")],
                    "left_pins": [controls.get("motor_right_forward")],
                    "right_pins": [controls.get("motor_right_backward")],
                },
            }
    camera = raw.get("camera")
    pipeline = camera.pop("pipeline", None) if isinstance(camera, dict) else None
    if "video" not in raw and pipeline is not None:
        if not isinstance(pipeline, str) or not pipeline.strip():
            raise ValueError("camera.pipeline must name a supported video profile")
        pipeline = pipeline.strip().lower()
        raw["video"] = {
            "type": {"libcamera": "ffmpeg_libcamera", "ffmpeg-libcamera": "ffmpeg_libcamera"}.get(
                pipeline, pipeline
            ),
            "options": {},
        }
    tts = raw.get("tts")
    if "tts" in raw and tts is None:
        raw["tts"] = {"enabled": False, "type": "none"}
    if isinstance(tts, dict):
        legacy = {key: tts.pop(key) for key in TTS_KEYS if key in tts}
        speaker = legacy.get("speaker_device") or legacy.get("audio_device")
        number = legacy.get("speaker_num") or legacy.get("hw_num")
        if not speaker and isinstance(number, str) and number.strip():
            speaker = f"plughw:{number.strip()}"
        if speaker and "playback_device" not in tts:
            tts["playback_device"] = speaker
        if "delay_ms" not in tts and legacy.get("delay_tts"):
            delay = legacy.get("delay", 0)
            if (
                not isinstance(delay, (int, float))
                or isinstance(delay, bool)
                or not math.isfinite(delay)
            ):
                raise ValueError("tts.delay must be numeric seconds")
            tts["delay_ms"] = int(delay * 1000)
        for old, new in (
            ("tts_volume", "volume"),
            ("filter_url_tts", "filter_urls"),
            ("anon_tts", "allow_anonymous"),
        ):
            if old in legacy and new not in tts:
                tts[new] = legacy[old]
        tts.setdefault("enabled", tts.get("type", "none") != "none")
    safety = raw.get("safety")
    if isinstance(safety, dict):
        safety.pop("emergency_stop_pin", None)
    for _section, block, aliases in _profile_blocks(raw):
        profile = _normalized_profile(block)
        if profile in aliases:
            block["type"] = aliases[profile]
    reject_legacy_config(raw)
    RobotConfig.model_validate(raw)
    return raw


def migrate_config_file(source: Path, output: Path) -> None:
    if source.resolve() == output.resolve():
        raise ValueError("migration output must differ from the original configuration")
    raw = yaml.safe_load(read_configuration_file(source).decode("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("configuration must contain a YAML object at the top level")
    content = yaml.safe_dump(migrate_config(raw), sort_keys=False, allow_unicode=False)
    # Exclusive creation refuses existing files and symlinks, including concurrent writers.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        output.unlink(missing_ok=True)
        raise
