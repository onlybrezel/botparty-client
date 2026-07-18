"""Shared BotParty client constants, state helpers, and small utilities."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import RobotConfig
from .redaction import redact_text
from .video.base import BaseVideoProfile

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


class MediaManager(Protocol):
    video_profile: BaseVideoProfile

    @property
    def frame_count(self) -> int: ...

    @property
    def video_track_published(self) -> bool: ...

    @property
    def audio_task(self) -> asyncio.Task[None] | None: ...

    async def run(
        self,
        room: Any,
        target_bitrate_kbps: int | None,
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None: ...

    def restart_audio(
        self,
        room: Any,
        running_fn: Callable[[], bool],
    ) -> asyncio.Task[None] | None: ...


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
    return not (
        logger_name == "root"
        and ("error running user callback for local_track_" in message or "KeyError:" in message)
    )


@dataclass(frozen=True, slots=True)
class DiagnosticRecord:
    sequence: int
    created_at: float
    line: str


class DiagnosticsBufferHandler(logging.Handler):
    def __init__(
        self,
        storage: deque[DiagnosticRecord],
        default_maxlen: int = 1000,
        redaction_literals: tuple[str, ...] = (),
    ) -> None:
        super().__init__(level=logging.INFO)
        self.storage = (
            storage if storage.maxlen is not None else deque(storage, maxlen=default_maxlen)
        )
        self._next_sequence = 1
        self._lock = threading.Lock()
        self._redaction_literals = redaction_literals

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = redact_text(self.format(record), self._redaction_literals)
            with self._lock:
                sequence = self._next_sequence
                self._next_sequence += 1
                self.storage.append(DiagnosticRecord(sequence, time.time(), line))
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
    control_disconnects: int = 0
    media_reconnects: int = 0
    safety_stops: int = 0
    watchdog_stops: int = 0
    emergency_stops: int = 0
    command_queue_drops: int = 0
    command_queue_high_watermark: int = 0
    stale_commands: int = 0
    last_command_ack_at: float = 0.0
    last_control_disconnect_reason: str | None = None
    last_stop_status: str = "never"
    last_stop_reason: str | None = None
    last_stop_error_code: str | None = None
    last_stop_at: float = 0.0
    claim_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    command_receive_latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    command_execution_ms: deque[float] = field(default_factory=lambda: deque(maxlen=512))
    stop_confirmation_ms: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    control_reconnect_ms: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    first_frame_ms: deque[float] = field(default_factory=lambda: deque(maxlen=256))
    media_restart_ms: deque[float] = field(default_factory=lambda: deque(maxlen=256))


@dataclass
class CameraRuntime:
    camera_id: str
    label: str
    role: str
    publish_mode: str
    config: RobotConfig
    video_profile: Any
    manager: MediaManager
    include_audio: bool = False
    task: asyncio.Task[None] | None = None
    restart_count: int = 0
    started_at_monotonic: float = 0.0
    last_frame_at_monotonic: float = 0.0
    last_frame_count: int = 0
    state: str = "stopped"
    last_error: str | None = None


@dataclass(slots=True)
class QueuedHardwareCommand:
    command: str
    value: Any
    metadata: dict[str, Any] | None
    motion_command_id: int | None


@dataclass(slots=True)
class QueuedTTSCommand:
    message: str
    metadata: dict[str, Any] | None
