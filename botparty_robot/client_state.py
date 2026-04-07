"""Shared BotParty client constants, state helpers, and small utilities."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

from .camera import CameraManager
from .config import RobotConfig

logger = logging.getLogger("botparty.client")
_SUPPRESS_LIVEKIT_NOISE_UNTIL = 0.0

TTS_SAY_COMMANDS = {"say", "speak", "tts", "tts:say", "tts.say"}
TTS_MUTE_COMMANDS = {"tts:mute", "tts.mute", "mute_tts", "tts_mute"}
TTS_UNMUTE_COMMANDS = {"tts:unmute", "tts.unmute", "unmute_tts", "tts_unmute"}
TTS_VOLUME_COMMANDS = {"tts:volume", "tts.volume", "tts_volume", "volume_tts"}
TELEMETRY_INTERVAL_SEC = 30
GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC = 25.0
LOCAL_GIT_STATUS_IGNORE_PATHS = (
    "config.yaml",
    "hardware_custom.py",
    ".venv/",
    "__pycache__/",
)


def suppress_livekit_reconnect_noise(duration_sec: float) -> None:
    global _SUPPRESS_LIVEKIT_NOISE_UNTIL
    _SUPPRESS_LIVEKIT_NOISE_UNTIL = max(
        _SUPPRESS_LIVEKIT_NOISE_UNTIL,
        time.time() + max(1.0, duration_sec),
    )


def should_emit_runtime_log(record: logging.LogRecord) -> bool:
    if time.time() >= _SUPPRESS_LIVEKIT_NOISE_UNTIL:
        return True

    logger_name = record.name or ""
    if logger_name.startswith("livekit"):
        return False

    message = record.getMessage()
    if logger_name == "root" and (
        "error running user callback for local_track_" in message
        or "KeyError:" in message
    ):
        return False

    return True


class DiagnosticsBufferHandler(logging.Handler):
    def __init__(self, storage: deque[str]) -> None:
        super().__init__(level=logging.INFO)
        self.storage = storage

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.storage.append(self.format(record))
        except Exception:
            pass


@dataclass
class WatchdogStats:
    """Runtime health counters."""

    camera_frames: int = 0
    commands_received: int = 0
    reconnect_attempts: int = 0
    last_heartbeat_at: float = field(default_factory=time.time)
    last_command_at: float = 0.0
    camera_task_restarts: int = 0


@dataclass
class CameraRuntime:
    camera_id: str
    label: str
    role: str
    publish_mode: str
    config: RobotConfig
    video_profile: Any
    manager: CameraManager
    include_audio: bool = False
    task: Optional[asyncio.Task] = None
    restart_count: int = 0
