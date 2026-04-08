"""Main BotParty robot client - connects to LiveKit and handles commands."""

from __future__ import annotations

import asyncio
import logging
import platform
from collections import deque
from pathlib import Path
from typing import Optional

import aiohttp
from livekit import rtc

from .camera import CameraManager
from .client_commands import ClientCommandsMixin
from .client_media import ClientMediaMixin
from .client_ops import ClientOpsMixin
from .client_runtime import ClientLifecycleMixin
from .client_state import (
    CameraRuntime,
    DiagnosticsBufferHandler,
    WatchdogStats,
    logger,
    should_emit_runtime_log,
)
from .config import RobotConfig
from .gateway import GatewayConnection
from .hardware import create_hardware
from .tts import create_tts_profile
from .video import create_video_profile


class BotPartyClient(
    ClientLifecycleMixin,
    ClientMediaMixin,
    ClientOpsMixin,
    ClientCommandsMixin,
):
    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.handler = create_hardware(config)
        self.tts = create_tts_profile(config)
        self._repo_root = Path(__file__).resolve().parents[1]
        (
            self._client_git_branch,
            self._client_git_commit,
            self._client_git_dirty,
        ) = self._read_git_metadata()
        self._python_version = platform.python_version()
        self._running = False
        self._room: Optional[rtc.Room] = None
        self._robot_id: Optional[str] = None
        self._configured_target_bitrate_kbps = self._parse_target_bitrate_kbps(
            self.config.video.options.get("target_bitrate_kbps")
        )
        self._remote_target_bitrate_kbps: Optional[int] = None
        self._livekit_connected = False
        self._livekit_publish_token: Optional[str] = None
        self._camera_runtimes = self._build_camera_runtimes()
        self._primary_camera_id = self._resolve_primary_camera_id()
        self._camera = (
            self._camera_runtimes[0].manager
            if self._camera_runtimes
            else CameraManager(config, create_video_profile(config))
        )
        self.video_profile = (
            self._camera_runtimes[0].video_profile
            if self._camera_runtimes
            else create_video_profile(config)
        )
        self._gateway = GatewayConnection(
            config,
            on_command=self._on_gateway_command,
            on_emergency_stop=lambda: self.handler.emergency_stop(),
            on_actions=self._apply_remote_actions_payload,
            on_shutdown=self._handle_gateway_shutdown,
            on_reconnected=self._handle_gateway_reconnected,
            on_disconnected=self._handle_gateway_disconnected,
            running_fn=lambda: self._running,
        )

        self._camera_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._actions_task: Optional[asyncio.Task] = None
        self._diag_upload_task: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._gateway_task: Optional[asyncio.Task] = None
        self._tts_queue: asyncio.Queue[tuple[str, dict[str, object] | None]] = asyncio.Queue(
            maxsize=20
        )
        self._hardware_lock = asyncio.Lock()
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason: Optional[str] = None
        self._planned_disconnect_notice_sent = False
        self._shutdown_disconnect_task: Optional[asyncio.Task] = None
        self._recovery_restart_task: Optional[asyncio.Task] = None
        self._livekit_reconnect_task: Optional[asyncio.Task] = None
        self._gateway_outage_started_at = 0.0
        self._gateway_outage_scope: Optional[str] = None
        self._livekit_disconnected_during_gateway_outage = False
        self._camera_restart_lock = asyncio.Lock()
        self._room_session_seq = 0
        self._active_room_session_id = 0
        self._active_room_disconnected_event: Optional[asyncio.Event] = None
        self._room_shutdown_task: Optional[asyncio.Task] = None
        self._room_reconnect_in_progress = False
        self._update_in_progress = False
        self._validate_media_mode()

        self.stats = WatchdogStats()
        self._diag_enabled_until = 0.0
        self._diag_buffer: deque[str] = deque(maxlen=400)
        self._diag_last_sent_idx = 0
        self._last_heartbeat_stale_warning_at = 0.0
        self._last_telemetry_sent_at = 0.0
        self._last_cpu_sample: Optional[tuple[float, float]] = None

        self._diag_handler = DiagnosticsBufferHandler(self._diag_buffer)
        self._diag_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger("botparty").addHandler(self._diag_handler)


__all__ = [
    "BotPartyClient",
    "CameraRuntime",
    "should_emit_runtime_log",
]
