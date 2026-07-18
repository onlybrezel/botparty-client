"""Main BotParty robot client - connects to LiveKit and handles commands."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
from collections import deque
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, Literal, cast

import aiohttp
from livekit import rtc

from .auth import AuthResult, ClientAuthenticator
from .capabilities import build_capability_manifest
from .client_commands import ClientCommandsComponent, CommandsHost
from .client_media import ClientMediaComponent, MediaHost
from .client_ops import ClientOpsComponent, OperationsHost
from .client_runtime import ClientLifecycleComponent, LifecycleHost
from .client_state import (
    CameraRuntime,
    DiagnosticRecord,
    DiagnosticsBufferHandler,
    QueuedTTSCommand,
    WatchdogStats,
    should_emit_runtime_log,
)
from .command_queue import HardwareCommandQueue
from .config import RobotConfig
from .device_state import resolve_state_directory
from .diagnostics import DiagnosticsUploader
from .faults import FaultRegistry
from .gateway import GatewayConnection
from .hardware import create_hardware
from .ota import UpdateManager
from .outbox import OutcomeOutbox
from .safety import SafetyController
from .tts import create_tts_profile


class BotPartyClient:
    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.lifecycle = ClientLifecycleComponent(cast(LifecycleHost, self))
        self.media = ClientMediaComponent(cast(MediaHost, self))
        self.operations = ClientOpsComponent(cast(OperationsHost, self))
        self.control = ClientCommandsComponent(cast(CommandsHost, self))
        self._safety = SafetyController()
        self._runtime_faults = FaultRegistry()
        self.handler = create_hardware(config)
        self.tts = create_tts_profile(config)
        self._repo_root = Path(__file__).resolve().parents[1]
        (
            self._client_git_branch,
            self._client_git_commit,
            self._client_git_dirty,
        ) = self.operations._read_git_metadata()
        self._python_version = platform.python_version()
        self._running = False
        self._room: rtc.Room | None = None
        self._robot_id: str | None = None
        self._configured_target_bitrate_kbps = self.media._parse_target_bitrate_kbps(
            self.config.video.options.get("target_bitrate_kbps")
        )
        self._remote_target_bitrate_kbps: int | None = None
        self._livekit_connected = False
        self._livekit_publish_token: str | None = None
        self._livekit_publish_tokens: dict[str, str] = {}
        (
            self._camera_runtimes,
            self._primary_camera_id,
        ) = self.media._build_initial_camera_runtime_state()
        self._gateway = GatewayConnection(
            config,
            on_command=self.control._on_gateway_command,
            on_emergency_stop=self.control._on_gateway_emergency_stop,
            on_actions=self.operations._apply_remote_actions_payload,
            session_provider=self.lifecycle._get_session,
            on_shutdown=self.media._handle_gateway_shutdown,
            on_reconnected=self.media._handle_gateway_reconnected,
            on_disconnected=self.media._handle_gateway_disconnected,
            on_reconnect_attempt=self.lifecycle._handle_gateway_reconnect_attempt,
            on_connected=self.control._on_gateway_connected,
            on_outcome_ack=self.control._on_outcome_ack,
            running_fn=lambda: self._running,
        )

        self._heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._actions_task: asyncio.Task[None] | None = None
        self._diag_upload_task: asyncio.Task[None] | None = None
        self._tts_task: asyncio.Task[None] | None = None
        self._gateway_task: asyncio.Task[None] | None = None
        self._command_task: asyncio.Task[None] | None = None
        self._health_task: asyncio.Task[None] | None = None
        self._health_started_event = asyncio.Event()
        self._health_start_error: str | None = None
        self._supervisor_last_tick_monotonic = 0.0
        self._systemd_ready_sent = False
        self._tts_queue: asyncio.Queue[QueuedTTSCommand] = asyncio.Queue(maxsize=20)
        self._hardware_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._stop_worker_task: asyncio.Task[None] | None = None
        self._hardware_safety_epoch = 0
        self._latest_motion_command_id = 0
        self._motion_deadline_handle: asyncio.TimerHandle | None = None
        self._motion_deadline_generation = 0
        self._processed_action_ids: deque[str] = deque(maxlen=1_024)
        self._processed_remote_action_ids: deque[str] = deque(maxlen=256)
        durable_outbox_enabled = bool(
            config._source_path is not None
            or config.state.directory is not None
            or os.getenv("BOTPARTY_STATE_DIR", "").strip()
        )
        self._outcome_outbox = (
            OutcomeOutbox(resolve_state_directory(config.state)) if durable_outbox_enabled else None
        )
        self._outbox_drain_lock = asyncio.Lock()
        self._hardware_commands = HardwareCommandQueue(self.config.safety.command_queue_size)
        self._http_session: aiohttp.ClientSession | None = None
        self._authenticator = ClientAuthenticator(config, self.lifecycle._get_session)
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason: str | None = None
        self._planned_disconnect_notice_sent = False
        self._shutdown_disconnect_task: asyncio.Task[None] | None = None
        self._recovery_restart_task: asyncio.Task[None] | None = None
        self._livekit_reconnect_task: asyncio.Task[None] | None = None
        self._gateway_outage_started_at = 0.0
        self._gateway_outage_scope: str | None = None
        self._livekit_disconnected_during_gateway_outage = False
        self._camera_restart_lock = asyncio.Lock()
        self._room_session_seq = 0
        self._active_room_session_id = 0
        self._active_room_disconnected_event: asyncio.Event | None = None
        self._room_shutdown_task: asyncio.Task[None] | None = None
        self._room_reconnect_in_progress = False
        self._media_connection_attempts = 0
        self._update_in_progress = False
        self._active_update_action_id: str | None = None
        self._update_manager = UpdateManager(config.ota) if config.ota.enabled else None
        self._ota_confirmed = False
        self._product_milestones: set[str] = set()
        if config.telemetry.product_analytics_enabled and config._legacy_migration_used:
            self._product_milestones.add("legacy_config_migrated")
        self.media._validate_media_mode()
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
            session=self.lifecycle._get_session,
            auth_token=self.config.server.robot_auth_token_value,
            client_id=lambda: self._robot_id,
        )
        self._last_heartbeat_stale_warning_at = 0.0
        self._last_telemetry_sent_at = 0.0
        self._last_cpu_sample: tuple[float, float] | None = None
        self.operations._prime_cpu_sample()  # Prime /proc/stat for the first telemetry delta.

        self._diag_handler = DiagnosticsBufferHandler(
            self._diag_buffer,
            redaction_literals=tuple(self.config.diagnostics.redaction_literals),
        )
        self._diag_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger("botparty").addHandler(self._diag_handler)

    async def run(self) -> None:
        await self.lifecycle.run()

    async def shutdown(self) -> None:
        await self.lifecycle.shutdown()

    def _record_fault(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None:
        self.lifecycle._record_fault(
            code,
            subsystem,
            retryable=retryable,
            safe_detail=safe_detail,
        )

    def _record_product_milestone(self, name: str) -> None:
        self.operations._record_product_milestone(name)

    def _start_background_task(
        self,
        awaitable: Coroutine[Any, Any, Any],
        name: str,
    ) -> None:
        self.control._start_background_task(awaitable, name)

    def _get_session(self) -> aiohttp.ClientSession:
        return self.lifecycle._get_session()

    def _get_safety_controller(self) -> SafetyController:
        return self.control._get_safety_controller()

    def _get_uptime_sec(self) -> int | None:
        return self.operations._get_uptime_sec()

    def _media_required(self) -> bool:
        return self.lifecycle._media_required()

    def _media_operational(self) -> bool:
        return self.lifecycle._media_operational()

    def _uses_direct_livekit_publisher(self) -> bool:
        return self.media._uses_direct_livekit_publisher()

    def _total_camera_frames(self) -> int:
        return self.media._total_camera_frames()

    def _default_target_bitrate_kbps(self, runtime: CameraRuntime | None = None) -> int:
        return self.media._default_target_bitrate_kbps(runtime)

    def _resolve_target_bitrate_kbps(
        self,
        *,
        remote: int | None,
        configured: int | None,
        default: int,
    ) -> int:
        return self.media._resolve_target_bitrate_kbps(
            remote=remote,
            configured=configured,
            default=default,
        )

    def _effective_target_bitrate_kbps(self) -> int:
        return self.media._effective_target_bitrate_kbps()

    def _build_health_snapshot(self) -> dict[str, object]:
        return self.lifecycle._build_health_snapshot()

    def _health_enabled(self) -> bool:
        return self.lifecycle._health_enabled()

    def _health_host(self) -> str:
        return self.lifecycle._health_host()

    def _health_port(self) -> int:
        return self.lifecycle._health_port()

    async def _authenticate(self) -> AuthResult | None:
        return await self.operations._authenticate()

    async def _actions_loop(self) -> None:
        await self.operations._actions_loop()

    async def _drain_outcome_outbox(self) -> None:
        await self.control._drain_outcome_outbox()

    async def _diagnostics_upload_loop(self) -> None:
        await self.operations._diagnostics_upload_loop()

    async def _heartbeat_loop(self) -> None:
        await self.operations._heartbeat_loop()

    async def _supervisor(self) -> None:
        await self.operations._supervisor()

    async def _tts_loop(self) -> None:
        await self.control._tts_loop()

    async def _hardware_command_loop(self) -> None:
        await self.control._hardware_command_loop()

    async def _start_all_cameras(self) -> None:
        await self.media._start_all_cameras()

    async def _sync_on_demand_cameras(self) -> None:
        await self.media._sync_on_demand_cameras()

    async def _stop_media_tasks(self) -> None:
        await self.media._stop_media_tasks()

    async def _restart_camera_pipeline(
        self,
        reason: str,
        camera_id: str | None = None,
    ) -> None:
        await self.media._restart_camera_pipeline(reason, camera_id)

    async def _trigger_hardware_stop(self, reason: str) -> bool:
        return await self.control._trigger_hardware_stop(reason)

    async def _perform_client_update(self, action_id: str | None = None) -> None:
        await self.operations._perform_client_update(action_id)

    async def _execute_action(self, action: dict[str, Any]) -> None:
        await self.control._execute_action(action)

    def _emit_remote_action_result(
        self,
        action_id: str,
        state: Literal["accepted", "completed", "rejected", "failed"],
        code: str,
    ) -> None:
        self.control._emit_remote_action_result(action_id, state, code)


__all__ = [
    "BotPartyClient",
    "CameraRuntime",
    "should_emit_runtime_log",
]
