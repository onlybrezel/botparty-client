"""Lifecycle helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import stat
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import aiohttp
from aiohttp import web
from livekit import rtc
from pydantic import SecretStr

from . import __build_id__, __version__
from .client_contract import ClientComponentBinding
from .client_state import logger
from .config import normalize_livekit_url
from .systemd import notify_systemd


class LifecycleHost(Protocol):
    config: Any
    handler: Any
    stats: Any
    _actions_task: asyncio.Task[None] | None
    _active_room_disconnected_event: asyncio.Event | None
    _active_room_session_id: int
    _camera_runtimes: list[Any]
    _capability_manifest: dict[str, object]
    _client_git_branch: str | None
    _client_git_commit: str | None
    _client_git_dirty: bool
    _command_task: asyncio.Task[None] | None
    _diag_upload_task: asyncio.Task[None] | None
    _gateway: Any
    _gateway_outage_started_at: float
    _gateway_task: asyncio.Task[None] | None
    _hardware_commands: Any
    _health_start_error: str | None
    _health_started_event: asyncio.Event
    _health_task: asyncio.Task[None] | None
    _heartbeat_task: asyncio.Task[None] | None
    _http_session: aiohttp.ClientSession | None
    _livekit_connected: bool
    _livekit_disconnected_during_gateway_outage: bool
    _livekit_publish_token: str | None
    _livekit_publish_tokens: dict[str, str]
    _livekit_reconnect_task: asyncio.Task[None] | None
    _media_connection_attempts: int
    _planned_disconnect_notice_sent: bool
    _planned_reconnect_at: float
    _planned_reconnect_reason: str | None
    _primary_camera_id: str
    _recovery_restart_task: asyncio.Task[None] | None
    _robot_id: str | None
    _room: Any
    _room_session_seq: int
    _room_shutdown_task: asyncio.Task[None] | None
    _running: bool
    _runtime_faults: Any
    _safety: Any
    _supervisor_last_tick_monotonic: float
    _tts_task: asyncio.Task[None] | None
    _update_in_progress: bool
    _watchdog_task: asyncio.Task[None] | None

    def _build_health_snapshot(self) -> dict[str, object]: ...

    def _get_safety_controller(self) -> Any: ...

    def _get_uptime_sec(self) -> int | None: ...

    def _media_operational(self) -> bool: ...

    def _media_required(self) -> bool: ...

    def _record_fault(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None: ...

    def _record_product_milestone(self, name: str) -> None: ...

    def _total_camera_frames(self) -> int: ...

    def _uses_direct_livekit_publisher(self) -> bool: ...

    async def _actions_loop(self) -> None: ...

    async def _authenticate(self) -> Any: ...

    async def _diagnostics_upload_loop(self) -> None: ...

    async def _hardware_command_loop(self) -> None: ...

    async def _heartbeat_loop(self) -> None: ...

    async def _start_all_cameras(self) -> None: ...

    async def _stop_media_tasks(self) -> None: ...

    async def _supervisor(self) -> None: ...

    async def _trigger_hardware_stop(self, reason: str) -> bool: ...

    async def _tts_loop(self) -> None: ...


class ClientLifecycleComponent(ClientComponentBinding[LifecycleHost]):
    def _record_fault(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None:
        registry = getattr(self.host, "_runtime_faults", None)
        if registry is not None:
            registry.record(
                code,
                subsystem,
                retryable=retryable,
                safe_detail=safe_detail,
            )

    def _handle_gateway_reconnect_attempt(self) -> None:
        self.host.stats.reconnect_attempts += 1

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if necessary."""
        if self.host._http_session is None or self.host._http_session.closed:
            self.host._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=30),
            )
        return self.host._http_session

    def _health_enabled(self) -> bool:
        raw = os.getenv("BOTPARTY_HEALTH_ENABLED", "true").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _health_host(self) -> str:
        return os.getenv("BOTPARTY_HEALTH_HOST", "127.0.0.1").strip() or "127.0.0.1"

    def _health_port(self) -> int:
        raw = os.getenv("BOTPARTY_HEALTH_PORT", "9100").strip()
        try:
            port = int(raw)
        except ValueError:
            return 9100
        return port if 1 <= port <= 65535 else 9100

    def _metrics_enabled(self) -> bool:
        return os.getenv("BOTPARTY_METRICS_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _build_health_snapshot(self) -> dict[str, object]:
        active_cameras = 0
        cameras: list[dict[str, object]] = []
        for runtime in self.host._camera_runtimes:
            task = runtime.task
            active = task is not None and not task.done()
            if active:
                active_cameras += 1
            cameras.append(
                {
                    "id": runtime.camera_id,
                    "label": runtime.label,
                    "role": runtime.role,
                    "publishMode": runtime.publish_mode,
                    "active": active,
                    "frames": runtime.manager.frame_count,
                    "restartCount": runtime.restart_count,
                    "audioEnabled": runtime.include_audio,
                    "state": getattr(runtime, "state", "streaming" if active else "stopped"),
                    "lastFrameAgeSec": self._camera_last_frame_age(runtime),
                    "lastError": getattr(runtime, "last_error", None),
                }
            )

        safety = self.host._get_safety_controller().snapshot()
        supervisor_age = (
            time.monotonic() - self.host._supervisor_last_tick_monotonic
            if self.host._supervisor_last_tick_monotonic > 0
            else None
        )
        live = bool(
            self.host._running
            and self.host._watchdog_task is not None
            and not self.host._watchdog_task.done()
            and supervisor_age is not None
            and supervisor_age <= 5.0
        )
        authenticated = bool(
            self.host._robot_id and self.host.config.server.robot_auth_token_value()
        )
        media_required = self.host._media_required()
        media_ready = self.host._media_operational()
        degraded: list[str] = []
        if not authenticated:
            degraded.append("auth")
        if not self.host._gateway.connected:
            degraded.append("control")
        health_ready = not self._health_enabled() or (
            self.host._health_started_event.is_set() and self.host._health_start_error is None
        )
        if not health_ready:
            degraded.append("health_server")
        if safety.latched:
            degraded.append("safety_latched")
        last_stop_status = getattr(self.host.stats, "last_stop_status", "never")
        stop_confirmed = last_stop_status in {"never", "confirmed"}
        if not stop_confirmed:
            degraded.append("safety_stop_unconfirmed")
        if media_required and not media_ready:
            degraded.append("media")
        if self.host._update_in_progress:
            degraded.append("update")
        ready = (
            live
            and authenticated
            and self.host._gateway.connected
            and health_ready
            and not safety.latched
            and stop_confirmed
            and not self.host._update_in_progress
        )
        if media_required:
            ready = ready and media_ready

        status = "stopped" if not self.host._running else ("ready" if ready else "degraded")

        return {
            "clientVersion": __version__,
            "build": {
                "id": __build_id__,
                "commit": self.host._client_git_commit,
                "branch": self.host._client_git_branch,
                "dirty": self.host._client_git_dirty,
            },
            "status": status,
            "robotId": self.host._robot_id,
            "gatewayConnected": self.host._gateway.connected,
            "livekitConnected": self.host._livekit_connected,
            "running": self.host._running,
            "live": live,
            "supervisorAgeSec": round(supervisor_age, 3) if supervisor_age is not None else None,
            "ready": ready,
            "degraded": degraded,
            "cameraCount": len(self.host._camera_runtimes),
            "activeCameras": active_cameras,
            "uptimeSec": self.host._get_uptime_sec(),
            "httpSessionOpen": self.host._http_session is not None
            and not self.host._http_session.closed,
            "stats": {
                "commandsReceived": self.host.stats.commands_received,
                "cameraFrames": self.host._total_camera_frames(),
                "reconnectAttempts": self.host.stats.reconnect_attempts,
                "cameraTaskRestarts": self.host.stats.camera_task_restarts,
                "controlDisconnects": self.host.stats.control_disconnects,
                "mediaReconnects": self.host.stats.media_reconnects,
                "safetyStops": self.host.stats.safety_stops,
                "watchdogStops": self.host.stats.watchdog_stops,
                "emergencyStops": self.host.stats.emergency_stops,
                "commandQueueDrops": self.host.stats.command_queue_drops,
                "commandQueueHighWatermark": self.host.stats.command_queue_high_watermark,
                "commandQueueDepth": len(self.host._hardware_commands),
                "staleCommands": self.host.stats.stale_commands,
                "lastCommandAckAgeSec": (
                    round(max(0.0, time.time() - self.host.stats.last_command_ack_at), 3)
                    if self.host.stats.last_command_ack_at > 0
                    else None
                ),
                "lastControlDisconnectReason": self.host.stats.last_control_disconnect_reason,
            },
            "safety": {
                "latched": safety.latched,
                "reason": safety.reason,
                "epoch": safety.epoch,
                "stopConfirmed": stop_confirmed,
                "lastStopStatus": last_stop_status,
                "lastStopReason": getattr(self.host.stats, "last_stop_reason", None),
                "lastStopErrorCode": getattr(self.host.stats, "last_stop_error_code", None),
                "lastStopAt": getattr(self.host.stats, "last_stop_at", 0.0) or None,
            },
            "audioSourceCameraId": next(
                (
                    runtime.camera_id
                    for runtime in self.host._camera_runtimes
                    if runtime.include_audio
                ),
                None,
            ),
            "update": {"inProgress": self.host._update_in_progress},
            "capabilities": self.host._capability_manifest,
            "faults": (
                self.host._runtime_faults.snapshot()
                if getattr(self.host, "_runtime_faults", None) is not None
                else []
            ),
            "cameras": cameras,
        }

    def _camera_last_frame_age(self, runtime: object) -> float | None:
        last_frame_at = float(getattr(runtime, "last_frame_at_monotonic", 0.0))
        if last_frame_at <= 0:
            return None
        return round(max(0.0, time.monotonic() - last_frame_at), 3)

    def _media_required(self) -> bool:
        for runtime in self.host._camera_runtimes:
            if (
                runtime.publish_mode == "on_demand"
                and runtime.camera_id != self.host._primary_camera_id
            ):
                continue
            profile = getattr(runtime, "video_profile", None)
            if profile is None or profile.capture_mode() != "none":
                return True
        return False

    def _media_operational(self) -> bool:
        if not self.host._media_required():
            return True
        if not self.host._livekit_connected:
            return False
        now = time.monotonic()
        for runtime in self.host._camera_runtimes:
            if (
                runtime.publish_mode == "on_demand"
                and runtime.camera_id != self.host._primary_camera_id
            ):
                continue
            task = runtime.task
            if task is None or task.done():
                return False
            frames = runtime.manager.frame_count
            if not runtime.manager.video_track_published:
                return False
            first_frame_count = int(getattr(runtime, "last_frame_count", 0))
            last_frame = float(getattr(runtime, "last_frame_at_monotonic", 0.0))
            if frames <= first_frame_count or last_frame <= 0:
                return False
            if last_frame > 0 and now - last_frame > 15:
                return False
        return True

    def _health_auth_token(self) -> str | None:
        path_value = os.getenv("BOTPARTY_HEALTH_AUTH_TOKEN_FILE", "").strip()
        if not path_value:
            return None
        try:
            path = Path(path_value)
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                return None
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
                return None
            return path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _health_authorized(self, request: web.Request) -> bool:
        token = self._health_auth_token()
        if token is None:
            return self._health_host() in {"127.0.0.1", "::1", "localhost"}
        supplied = request.headers.get("Authorization", "")
        return secrets.compare_digest(supplied, f"Bearer {token}")

    async def _handle_health_request(self, request: web.Request) -> web.Response:
        if not self._health_authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        path = request.path
        snapshot = self.host._build_health_snapshot()
        status = 200
        if path == "/ready" and not snapshot["ready"]:
            status = 503
        if path == "/live" and not snapshot["live"]:
            status = 503
        response = web.json_response(snapshot, status=status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def _handle_metrics_request(self, request: web.Request) -> web.Response:
        if not self._health_authorized(request):
            return web.Response(text="unauthorized\n", status=401)
        snapshot = self.host._build_health_snapshot()
        stats = snapshot["stats"]
        assert isinstance(stats, dict)
        safety = snapshot["safety"]
        assert isinstance(safety, dict)

        def quantile(values: Sequence[float], fraction: float) -> float:
            samples = sorted(float(value) for value in values)
            if not samples:
                return 0.0
            index = min(len(samples) - 1, max(0, int(len(samples) * fraction + 0.999) - 1))
            return samples[index]

        outbox = getattr(self.host, "_outcome_outbox", None)
        values = {
            "botparty_ready": int(bool(snapshot["ready"])),
            "botparty_live": int(bool(snapshot["live"])),
            "botparty_gateway_connected": int(bool(snapshot["gatewayConnected"])),
            "botparty_active_cameras": (
                int(snapshot["activeCameras"])
                if isinstance(snapshot["activeCameras"], (int, float, str))
                else 0
            ),
            "botparty_commands_received_total": int(stats["commandsReceived"]),
            "botparty_camera_restarts_total": int(stats["cameraTaskRestarts"]),
            "botparty_control_disconnects_total": int(stats["controlDisconnects"]),
            "botparty_safety_stops_total": int(stats["safetyStops"]),
            "botparty_watchdog_stops_total": int(stats["watchdogStops"]),
            "botparty_safety_latched": int(bool(safety["latched"])),
            "botparty_safety_stop_confirmed": int(bool(safety["stopConfirmed"])),
            "botparty_claim_latency_ms_p95": quantile(self.host.stats.claim_latency_ms, 0.95),
            "botparty_command_receive_latency_ms_p99": quantile(
                self.host.stats.command_receive_latency_ms, 0.99
            ),
            "botparty_command_execution_ms_p99": quantile(
                self.host.stats.command_execution_ms, 0.99
            ),
            "botparty_stop_confirmation_ms_p99": quantile(
                self.host.stats.stop_confirmation_ms, 0.99
            ),
            "botparty_control_reconnect_ms_p95": quantile(
                self.host.stats.control_reconnect_ms, 0.95
            ),
            "botparty_first_frame_ms_p95": quantile(self.host.stats.first_frame_ms, 0.95),
            "botparty_media_restart_ms_p95": quantile(self.host.stats.media_restart_ms, 0.95),
            "botparty_outbox_pending": outbox.pending_count() if outbox is not None else 0,
            "botparty_outbox_oldest_seconds": (
                outbox.oldest_pending_age_seconds() if outbox is not None else 0
            ),
        }
        lines = [
            "# BotParty operational metrics. No user, command or media labels are exported.",
            *(f"{name} {value}" for name, value in values.items()),
            "",
        ]
        return web.Response(
            text="\n".join(lines),
            content_type="text/plain",
            charset="utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    async def _run_health_server(self) -> None:
        if not self._health_enabled():
            return

        host = self._health_host()
        port = self._health_port()
        app = web.Application()
        app.router.add_get("/health", self._handle_health_request)
        app.router.add_get("/live", self._handle_health_request)
        app.router.add_get("/ready", self._handle_health_request)
        if self._metrics_enabled():
            app.router.add_get("/metrics", self._handle_metrics_request)

        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            self.host._health_start_error = None
            self.host._health_started_event.set()
            logger.info("Local health endpoint listening on http://%s:%d/health", host, port)
        except OSError as exc:
            self.host._health_start_error = type(exc).__name__
            self.host._record_fault(
                "health_bind_failed",
                "health",
                retryable=False,
                safe_detail=type(exc).__name__,
            )
            self.host._health_started_event.set()
            logger.error("Failed to start local health endpoint on %s:%d: %s", host, port, exc)
            with contextlib.suppress(Exception):
                await runner.cleanup()
            raise RuntimeError("configured health endpoint could not be started") from exc

        try:
            while self.host._running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await runner.cleanup()

    async def run(self) -> None:
        self.host._running = True
        self._ensure_background_tasks()
        if self._health_enabled():
            await self.host._health_started_event.wait()
            if self.host._health_start_error is not None:
                raise RuntimeError(
                    f"health server failed during startup: {self.host._health_start_error}"
                )
        while self.host._running:
            auth = await self.host._authenticate()
            if auth is None:
                logger.error("Authentication failed. Retrying in 5s.")
                await asyncio.sleep(5)
                continue

            self.host._robot_id = auth.robot_id
            self.host._livekit_publish_token = auth.token
            self.host._livekit_publish_tokens = auth.publish_tokens
            self.host.config.server.robot_auth_token = SecretStr(auth.robot_auth_token)
            if auth.livekit_url:
                livekit_url = normalize_livekit_url(auth.livekit_url)
                if livekit_url != self.host.config.server.livekit_url:
                    logger.info("Using LiveKit URL from claim response: %s", livekit_url)
                    self.host.config.server.livekit_url = livekit_url
            logger.info("Authenticated as robot %s", auth.robot_id)
            self.host._record_product_milestone("claimed")

            # Control, health and safety supervision must remain available even
            # when camera or LiveKit setup is degraded.
            self._ensure_background_tasks()
            notify_systemd("STATUS=Control plane starting")

            if self.host._uses_direct_livekit_publisher():
                await self._connect_direct_livekit()
            else:
                await self._connect(auth.token)

            if not self.host._running:
                break
            await asyncio.sleep(self._consume_reconnect_delay(default_delay=2.0))

    async def _connect(self, token: str) -> None:
        self._mark_media_connection_attempt()
        self.host._room_session_seq += 1
        session_id = self.host._room_session_seq
        room = rtc.Room()
        room_disconnected_event = asyncio.Event()
        self.host._room = room
        self.host._active_room_session_id = session_id
        self.host._active_room_disconnected_event = room_disconnected_event
        self.host.stats.camera_task_restarts = 0

        @room.on("disconnected")
        def on_disconnected() -> None:
            room_disconnected_event.set()
            is_current_room = (
                self.host._room is room and self.host._active_room_session_id == session_id
            )
            if not is_current_room:
                logger.debug(
                    "Ignoring disconnect callback from stale LiveKit room session %s",
                    session_id,
                )
                return
            if self.host._gateway_outage_started_at > 0:
                self.host._livekit_disconnected_during_gateway_outage = True
            if self.host._planned_reconnect_at > time.time():
                logger.info(
                    "Disconnected from LiveKit room for planned %s window",
                    self.host._planned_reconnect_reason or "restart",
                )
            else:
                logger.warning("Disconnected from LiveKit room")
            if self.host._running:
                self.host._livekit_connected = False
                if self.host._room_shutdown_task is None or self.host._room_shutdown_task.done():
                    self.host._room_shutdown_task = asyncio.create_task(
                        self.host._stop_media_tasks()
                    )

        try:
            await room.connect(self.host.config.server.livekit_url, token)
            self.host._livekit_connected = True
            self.host._planned_disconnect_notice_sent = False
            logger.info("Connected to LiveKit room: robot-%s", self.host._robot_id)

            await self.host._start_all_cameras()
            self._ensure_background_tasks()

            while self.host._running and self.host._livekit_connected:
                await asyncio.sleep(1)
        except Exception as exc:
            logger.error("LiveKit connection error: %s", exc)
            self.host._livekit_connected = False
        finally:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(room_disconnected_event.wait(), timeout=6)

            shutdown_task = self.host._room_shutdown_task
            if shutdown_task and not shutdown_task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await shutdown_task

            if self.host._room is room and self.host._active_room_session_id == session_id:
                self.host._room = None
                self.host._active_room_disconnected_event = None

    async def _connect_direct_livekit(self) -> None:
        self._mark_media_connection_attempt()
        if not self.host._livekit_publish_token:
            logger.error("Claim response did not include a LiveKit publish token")
            await asyncio.sleep(5)
            return

        self.host._room = None
        self.host._active_room_disconnected_event = None
        self.host.stats.camera_task_restarts = 0
        self.host._livekit_connected = True
        logger.info(
            "Connected for direct video publishing: %s", self.host.config.server.livekit_url
        )

        try:
            await self.host._start_all_cameras()
            self._ensure_background_tasks()

            while self.host._running and self.host._livekit_connected:
                await asyncio.sleep(1)
        finally:
            self.host._livekit_connected = False
            await self.host._stop_media_tasks()

    def _mark_media_connection_attempt(self) -> None:
        self.host._media_connection_attempts += 1
        if self.host._media_connection_attempts > 1:
            self.host.stats.media_reconnects += 1

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        notify_systemd("STOPPING=1\nSTATUS=Applying safe stop")
        self.host._running = False
        self.host._livekit_connected = False
        stop_confirmed = await self.host._trigger_hardware_stop("shutdown")

        for task in [
            *(runtime.task for runtime in self.host._camera_runtimes),
            self.host._tts_task,
            self.host._heartbeat_task,
            self.host._watchdog_task,
            self.host._actions_task,
            self.host._diag_upload_task,
            self.host._gateway_task,
            self.host._command_task,
            self.host._health_task,
            self.host._recovery_restart_task,
            self.host._livekit_reconnect_task,
            self.host._room_shutdown_task,
        ]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        if self.host._room:
            await self.host._room.disconnect()

        if self.host._http_session and not self.host._http_session.closed:
            await self.host._http_session.close()

        close_confirmed = True
        try:
            await asyncio.wait_for(asyncio.to_thread(self.host.handler.close), timeout=3.0)
        except asyncio.TimeoutError:
            close_confirmed = False
            self.host._record_fault(
                "hardware_close_timeout",
                "safety",
                retryable=False,
                safe_detail="shutdown",
            )
            logger.error("Hardware close timed out during shutdown")
        except Exception as exc:
            close_confirmed = False
            self.host._record_fault(
                "hardware_close_failed",
                "safety",
                retryable=False,
                safe_detail=type(exc).__name__,
            )
            logger.error("Hardware close failed during shutdown: %s", exc)

        if not stop_confirmed or not close_confirmed:
            logger.error(
                "Shutdown failed safety confirmation: stop=%s close=%s",
                stop_confirmed,
                close_confirmed,
            )
            raise RuntimeError("safe shutdown was not confirmed")
        logger.info(
            "Shutdown complete: commands=%d frames=%d reconnects=%d",
            self.host.stats.commands_received,
            self.host._total_camera_frames(),
            self.host.stats.reconnect_attempts,
        )

    def _ensure_background_tasks(self) -> None:
        if self.host._heartbeat_task is None or self.host._heartbeat_task.done():
            self.host._heartbeat_task = asyncio.create_task(self.host._heartbeat_loop())
        if self.host._watchdog_task is None or self.host._watchdog_task.done():
            self.host._watchdog_task = asyncio.create_task(self.host._supervisor())
        if self.host._actions_task is None or self.host._actions_task.done():
            self.host._actions_task = asyncio.create_task(self.host._actions_loop())
        if self.host._diag_upload_task is None or self.host._diag_upload_task.done():
            self.host._diag_upload_task = asyncio.create_task(self.host._diagnostics_upload_loop())
        if self.host._tts_task is None or self.host._tts_task.done():
            self.host._tts_task = asyncio.create_task(self.host._tts_loop())
        if self.host._gateway_task is None or self.host._gateway_task.done():
            self.host._gateway_task = asyncio.create_task(self.host._gateway.run())
        if self.host._command_task is None or self.host._command_task.done():
            self.host._command_task = asyncio.create_task(self.host._hardware_command_loop())
        if self.host._health_task is None or self.host._health_task.done():
            self.host._health_task = asyncio.create_task(self._run_health_server())

    def _consume_reconnect_delay(self, default_delay: float) -> float:
        planned_reconnect_at = self.host._planned_reconnect_at
        self.host._planned_reconnect_at = 0.0
        self.host._planned_reconnect_reason = None
        if planned_reconnect_at <= 0:
            return default_delay
        return max(default_delay, planned_reconnect_at - time.time())


ClientLifecycleMixin = ClientLifecycleComponent
