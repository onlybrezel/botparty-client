"""Main BotParty robot client - connects to LiveKit and handles commands."""

from __future__ import annotations

import asyncio
import logging
import platform
from collections import deque
from pathlib import Path

import aiohttp
from livekit import rtc

from .auth import ClientAuthenticator
from .capabilities import build_capability_manifest
from .client_commands import ClientCommandsMixin
from .client_media import ClientMediaMixin
from .client_ops import ClientOpsMixin
from .client_runtime import ClientLifecycleMixin
from .client_state import (
    CameraRuntime,
    DiagnosticRecord,
    DiagnosticsBufferHandler,
    QueuedHardwareCommand,
    WatchdogStats,
    should_emit_runtime_log,
)
from .config import RobotConfig
from .diagnostics import DiagnosticsUploader
from .gateway import GatewayConnection
from .hardware import create_hardware
from .ota import UpdateManager
from .safety import SafetyController
from .tts import create_tts_profile


class BotPartyClient(
    ClientLifecycleMixin,
    ClientMediaMixin,
    ClientOpsMixin,
    ClientCommandsMixin,
):
    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self._safety = SafetyController()
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
        self._room: rtc.Room | None = None
        self._robot_id: str | None = None
        self._configured_target_bitrate_kbps = self._parse_target_bitrate_kbps(
            self.config.video.options.get("target_bitrate_kbps")
        )
        self._remote_target_bitrate_kbps: int | None = None
        self._livekit_connected = False
        self._livekit_publish_token: str | None = None
        self._livekit_publish_tokens: dict[str, str] = {}
        self._camera_runtimes, self._primary_camera_id = self._build_initial_camera_runtime_state()
        self._gateway = GatewayConnection(
            config,
            on_command=self._on_gateway_command,
            on_emergency_stop=self._on_gateway_emergency_stop,
            on_actions=self._apply_remote_actions_payload,
            session_provider=self._get_session,
            on_shutdown=self._handle_gateway_shutdown,
            on_reconnected=self._handle_gateway_reconnected,
            on_disconnected=self._handle_gateway_disconnected,
            on_reconnect_attempt=self._handle_gateway_reconnect_attempt,
            running_fn=lambda: self._running,
        )

        self._heartbeat_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._actions_task: asyncio.Task | None = None
        self._diag_upload_task: asyncio.Task | None = None
        self._tts_task: asyncio.Task | None = None
        self._gateway_task: asyncio.Task | None = None
        self._command_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._tts_queue: asyncio.Queue[tuple[str, dict[str, object] | None]] = asyncio.Queue(
            maxsize=20
        )
        self._hardware_lock = asyncio.Lock()
        self._hardware_safety_epoch = 0
        self._latest_motion_command_id = 0
        self._motion_deadline_handle: asyncio.TimerHandle | None = None
        self._motion_deadline_generation = 0
        self._processed_action_ids: deque[str] = deque(maxlen=1_024)
        self._processed_remote_action_ids: deque[str] = deque(maxlen=256)
        self._command_queue: deque[QueuedHardwareCommand] = deque(
            maxlen=self.config.safety.command_queue_size
        )
        self._command_queue_event = asyncio.Event()
        self._http_session: aiohttp.ClientSession | None = None
        self._authenticator = ClientAuthenticator(config, self._get_session)
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason: str | None = None
        self._planned_disconnect_notice_sent = False
        self._shutdown_disconnect_task: asyncio.Task | None = None
        self._recovery_restart_task: asyncio.Task | None = None
        self._livekit_reconnect_task: asyncio.Task | None = None
        self._gateway_outage_started_at = 0.0
        self._gateway_outage_scope: str | None = None
        self._livekit_disconnected_during_gateway_outage = False
        self._camera_restart_lock = asyncio.Lock()
        self._room_session_seq = 0
        self._active_room_session_id = 0
        self._active_room_disconnected_event: asyncio.Event | None = None
        self._room_shutdown_task: asyncio.Task | None = None
        self._room_reconnect_in_progress = False
        self._media_connection_attempts = 0
        self._update_in_progress = False
        self._update_manager = UpdateManager(config.ota) if config.ota.enabled else None
        self._ota_confirmed = False
        self._product_milestones: set[str] = set()
        self._validate_media_mode()
        self._capability_manifest = build_capability_manifest(
            self.config,
            self.handler,
            self._camera_runtimes,
            self.tts,
        )

        self.stats = WatchdogStats()
        self._diag_enabled_until = 0.0
        self._diag_buffer: deque[DiagnosticRecord] = deque(
            maxlen=self.config.diagnostics.buffer_lines
        )
        self._diagnostics_uploader = DiagnosticsUploader(
            config=self.config.diagnostics,
            api_url=self.config.server.api_url,
            records=self._diag_buffer,
            session=self._get_session,
            auth_token=self.config.server.robot_auth_token_value,
            client_id=lambda: self._robot_id,
        )
        self._last_heartbeat_stale_warning_at = 0.0
        self._last_telemetry_sent_at = 0.0
        self._last_cpu_sample: tuple[float, float] | None = None
        self._prime_cpu_sample()  # Read initial /proc/stat so first telemetry has a delta

        self._diag_handler = DiagnosticsBufferHandler(
            self._diag_buffer,
            redaction_literals=tuple(self.config.diagnostics.redaction_literals),
        )
        self._diag_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger("botparty").addHandler(self._diag_handler)


__all__ = [
    "BotPartyClient",
    "CameraRuntime",
    "should_emit_runtime_log",
]
