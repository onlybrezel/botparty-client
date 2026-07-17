"""Deterministic robot capability manifest."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from . import __version__
from .config import RobotConfig
from .hardware.base import BaseHardware
from .tts.base import BaseTTSProfile


def build_capability_manifest(
    config: RobotConfig,
    hardware: BaseHardware,
    camera_runtimes: list[Any],
    tts: BaseTTSProfile,
) -> dict[str, Any]:
    hardware_capabilities = hardware.capabilities()
    cameras = [
        {
            "id": runtime.camera_id,
            "role": runtime.role,
            "videoProfile": runtime.video_profile.profile_name,
            "transport": runtime.video_profile.publish_transport(),
            "audio": runtime.include_audio,
            "inputFormat": runtime.config.camera.fourcc or "auto",
            "publishFormat": (
                "H264" if runtime.video_profile.publish_transport() == "livekit_direct" else "RGBA"
            ),
            "width": runtime.config.camera.width,
            "height": runtime.config.camera.height,
            "fps": runtime.config.camera.fps,
        }
        for runtime in camera_runtimes
    ]
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "hardware": {
            "adapter": hardware.profile_name,
            "adapterVersion": __version__,
            "commands": list(hardware_capabilities.commands),
            "motionCommands": list(hardware_capabilities.motion_commands),
            "safeStop": hardware_capabilities.safe_stop,
            "safeStopType": "software_latched_deenergize",
            "close": hardware_capabilities.close,
            "supportLevel": hardware_capabilities.support_level,
            "valueRanges": {},
        },
        "safety": {
            "maxRunTimeMs": config.safety.max_run_time_ms,
            "commandTtlMs": config.safety.command_ttl_ms,
            "stopTimeoutMs": config.safety.stop_timeout_ms,
            "requiresMediaForMotion": config.safety.require_media_for_motion,
        },
        "cameras": cameras,
        "audioSourceCameraId": next(
            (runtime.camera_id for runtime in camera_runtimes if runtime.include_audio),
            None,
        ),
        "tts": {
            "enabled": config.tts.enabled,
            "profile": tts.profile_name,
            "cloud": tts.profile_name in {"polly", "google_cloud"},
            "maxCharacters": config.tts.max_characters,
            "dailyCharacterBudget": config.tts.daily_character_budget,
        },
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    manifest["hash"] = hashlib.sha256(encoded).hexdigest()
    return manifest
