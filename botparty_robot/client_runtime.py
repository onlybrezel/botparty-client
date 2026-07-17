"""Lifecycle helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import stat
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from livekit import rtc
from pydantic import SecretStr

from . import __build_id__, __version__
from .client_state import logger
from .config import normalize_livekit_url
from .systemd import notify_systemd


class ClientLifecycleMixin:
    def _handle_gateway_reconnect_attempt(self) -> None:
        self.stats.reconnect_attempts += 1

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if necessary."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_read=30),
            )
        return self._http_session

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

    def _build_health_snapshot(self) -> dict[str, object]:
        active_cameras = 0
        cameras: list[dict[str, object]] = []
        for runtime in self._camera_runtimes:
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

        safety = self._get_safety_controller().snapshot()
        live = self._running and (self._watchdog_task is None or not self._watchdog_task.done())
        authenticated = bool(self._robot_id and self.config.server.robot_auth_token_value())
        media_required = self._media_required()
        media_ready = self._media_operational()
        degraded: list[str] = []
        if not authenticated:
            degraded.append("auth")
        if not self._gateway.connected:
            degraded.append("control")
        if safety.latched:
            degraded.append("safety_latched")
        if media_required and not media_ready:
            degraded.append("media")
        if self._update_in_progress:
            degraded.append("update")
        ready = (
            live
            and authenticated
            and self._gateway.connected
            and not safety.latched
            and not self._update_in_progress
        )
        if media_required:
            ready = ready and media_ready

        status = "stopped" if not self._running else ("ready" if ready else "degraded")

        return {
            "clientVersion": __version__,
            "build": {
                "id": __build_id__,
                "commit": self._client_git_commit,
                "branch": self._client_git_branch,
                "dirty": self._client_git_dirty,
            },
            "status": status,
            "robotId": self._robot_id,
            "gatewayConnected": self._gateway.connected,
            "livekitConnected": self._livekit_connected,
            "running": self._running,
            "live": live,
            "ready": ready,
            "degraded": degraded,
            "cameraCount": len(self._camera_runtimes),
            "activeCameras": active_cameras,
            "uptimeSec": self._get_uptime_sec(),
            "httpSessionOpen": self._http_session is not None and not self._http_session.closed,
            "stats": {
                "commandsReceived": self.stats.commands_received,
                "cameraFrames": self._total_camera_frames(),
                "reconnectAttempts": self.stats.reconnect_attempts,
                "cameraTaskRestarts": self.stats.camera_task_restarts,
                "controlDisconnects": self.stats.control_disconnects,
                "mediaReconnects": self.stats.media_reconnects,
                "safetyStops": self.stats.safety_stops,
                "watchdogStops": self.stats.watchdog_stops,
                "emergencyStops": self.stats.emergency_stops,
                "commandQueueDrops": self.stats.command_queue_drops,
                "commandQueueHighWatermark": self.stats.command_queue_high_watermark,
                "commandQueueDepth": len(self._command_queue),
                "staleCommands": self.stats.stale_commands,
                "lastCommandAckAgeSec": (
                    round(max(0.0, time.time() - self.stats.last_command_ack_at), 3)
                    if self.stats.last_command_ack_at > 0
                    else None
                ),
                "lastControlDisconnectReason": self.stats.last_control_disconnect_reason,
            },
            "safety": {
                "latched": safety.latched,
                "reason": safety.reason,
                "epoch": safety.epoch,
            },
            "audioSourceCameraId": next(
                (runtime.camera_id for runtime in self._camera_runtimes if runtime.include_audio),
                None,
            ),
            "update": {"inProgress": self._update_in_progress},
            "capabilities": self._capability_manifest,
            "cameras": cameras,
        }

    def _camera_last_frame_age(self, runtime: object) -> float | None:
        last_frame_at = float(getattr(runtime, "last_frame_at_monotonic", 0.0))
        if last_frame_at <= 0:
            return None
        return round(max(0.0, time.monotonic() - last_frame_at), 3)

    def _media_required(self) -> bool:
        for runtime in self._camera_runtimes:
            profile = getattr(runtime, "video_profile", None)
            if profile is None or profile.capture_mode() != "none":
                return True
        return False

    def _media_operational(self) -> bool:
        if not self._media_required():
            return True
        if not self._livekit_connected:
            return False
        now = time.monotonic()
        for runtime in self._camera_runtimes:
            task = runtime.task
            if task is None or task.done():
                return False
            frames = runtime.manager.frame_count
            started = float(getattr(runtime, "started_at_monotonic", now))
            last_frame = float(getattr(runtime, "last_frame_at_monotonic", 0.0))
            if frames <= 0 and now - started > 15:
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
        snapshot = self._build_health_snapshot()
        status = 200
        if path == "/ready" and not snapshot["ready"]:
            status = 503
        if path == "/live" and not snapshot["live"]:
            status = 503
        response = web.json_response(snapshot, status=status)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def _run_health_server(self) -> None:
        if not self._health_enabled():
            return

        host = self._health_host()
        port = self._health_port()
        app = web.Application()
        app.router.add_get("/health", self._handle_health_request)
        app.router.add_get("/live", self._handle_health_request)
        app.router.add_get("/ready", self._handle_health_request)

        runner = web.AppRunner(app, access_log=None)
        try:
            await runner.setup()
            site = web.TCPSite(runner, host, port)
            await site.start()
            logger.info("Local health endpoint listening on http://%s:%d/health", host, port)
        except OSError as exc:
            logger.warning("Failed to start local health endpoint on %s:%d: %s", host, port, exc)
            with contextlib.suppress(Exception):
                await runner.cleanup()
            return

        try:
            while self._running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await runner.cleanup()

    async def run(self) -> None:
        self._running = True
        while self._running:
            auth = await self._authenticate()
            if auth is None:
                logger.error("Authentication failed. Retrying in 5s.")
                await asyncio.sleep(5)
                continue

            self._robot_id = auth.robot_id
            self._livekit_publish_token = auth.token
            self._livekit_publish_tokens = auth.publish_tokens
            self.config.server.robot_auth_token = SecretStr(auth.robot_auth_token)
            if auth.livekit_url:
                livekit_url = normalize_livekit_url(auth.livekit_url)
                if livekit_url != self.config.server.livekit_url:
                    logger.info("Using LiveKit URL from claim response: %s", livekit_url)
                    self.config.server.livekit_url = livekit_url
            logger.info("Authenticated as robot %s", auth.robot_id)
            self._record_product_milestone("claimed")

            # Control, health and safety supervision must remain available even
            # when camera or LiveKit setup is degraded.
            self._ensure_background_tasks()
            notify_systemd("READY=1\nSTATUS=Control plane starting")

            if self._uses_direct_livekit_publisher():
                await self._connect_direct_livekit()
            else:
                await self._connect(auth.token)

            if not self._running:
                break
            await asyncio.sleep(self._consume_reconnect_delay(default_delay=2.0))

    async def _connect(self, token: str) -> None:
        self._mark_media_connection_attempt()
        self._room_session_seq += 1
        session_id = self._room_session_seq
        room = rtc.Room()
        room_disconnected_event = asyncio.Event()
        self._room = room
        self._active_room_session_id = session_id
        self._active_room_disconnected_event = room_disconnected_event
        self.stats.camera_task_restarts = 0

        @room.on("disconnected")
        def on_disconnected() -> None:
            room_disconnected_event.set()
            is_current_room = self._room is room and self._active_room_session_id == session_id
            if not is_current_room:
                logger.debug(
                    "Ignoring disconnect callback from stale LiveKit room session %s",
                    session_id,
                )
                return
            if self._gateway_outage_started_at > 0:
                self._livekit_disconnected_during_gateway_outage = True
            if self._planned_reconnect_at > time.time():
                logger.info(
                    "Disconnected from LiveKit room for planned %s window",
                    self._planned_reconnect_reason or "restart",
                )
            else:
                logger.warning("Disconnected from LiveKit room")
            if self._running:
                self._livekit_connected = False
                if self._room_shutdown_task is None or self._room_shutdown_task.done():
                    self._room_shutdown_task = asyncio.create_task(self._stop_media_tasks())

        try:
            await room.connect(self.config.server.livekit_url, token)
            self._livekit_connected = True
            self._planned_disconnect_notice_sent = False
            logger.info("Connected to LiveKit room: robot-%s", self._robot_id)

            await self._start_all_cameras()
            self._ensure_background_tasks()

            while self._running and self._livekit_connected:
                await asyncio.sleep(1)
        except Exception as exc:
            logger.error("LiveKit connection error: %s", exc)
            self._livekit_connected = False
        finally:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(room_disconnected_event.wait(), timeout=6)

            shutdown_task = self._room_shutdown_task
            if shutdown_task and not shutdown_task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await shutdown_task

            if self._room is room and self._active_room_session_id == session_id:
                self._room = None
                self._active_room_disconnected_event = None

    async def _connect_direct_livekit(self) -> None:
        self._mark_media_connection_attempt()
        if not self._livekit_publish_token:
            logger.error("Claim response did not include a LiveKit publish token")
            await asyncio.sleep(5)
            return

        self._room = None
        self._active_room_disconnected_event = None
        self.stats.camera_task_restarts = 0
        self._livekit_connected = True
        logger.info("Connected for direct video publishing: %s", self.config.server.livekit_url)

        try:
            await self._start_all_cameras()
            self._ensure_background_tasks()

            while self._running and self._livekit_connected:
                await asyncio.sleep(1)
        finally:
            self._livekit_connected = False
            await self._stop_media_tasks()

    def _mark_media_connection_attempt(self) -> None:
        self._media_connection_attempts += 1
        if self._media_connection_attempts > 1:
            self.stats.media_reconnects += 1

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        notify_systemd("STOPPING=1\nSTATUS=Applying safe stop")
        self._running = False
        self._livekit_connected = False
        await self._trigger_hardware_stop("shutdown")

        for task in [
            *(runtime.task for runtime in self._camera_runtimes),
            self._tts_task,
            self._heartbeat_task,
            self._watchdog_task,
            self._actions_task,
            self._diag_upload_task,
            self._gateway_task,
            self._command_task,
            self._health_task,
            self._recovery_restart_task,
            self._livekit_reconnect_task,
            self._room_shutdown_task,
        ]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        if self._room:
            await self._room.disconnect()

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.to_thread(self.handler.close), timeout=3.0)

        logger.info(
            "Goodbye! Stats: commands=%d frames=%d reconnects=%d",
            self.stats.commands_received,
            self._total_camera_frames(),
            self.stats.reconnect_attempts,
        )

    def _ensure_background_tasks(self) -> None:
        if self._heartbeat_task is None or self._heartbeat_task.done():
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._supervisor())
        if self._actions_task is None or self._actions_task.done():
            self._actions_task = asyncio.create_task(self._actions_loop())
        if self._diag_upload_task is None or self._diag_upload_task.done():
            self._diag_upload_task = asyncio.create_task(self._diagnostics_upload_loop())
        if self._tts_task is None or self._tts_task.done():
            self._tts_task = asyncio.create_task(self._tts_loop())
        if self._gateway_task is None or self._gateway_task.done():
            self._gateway_task = asyncio.create_task(self._gateway.run())
        if self._command_task is None or self._command_task.done():
            self._command_task = asyncio.create_task(self._hardware_command_loop())
        if self._health_task is None or self._health_task.done():
            self._health_task = asyncio.create_task(self._run_health_server())

    def _consume_reconnect_delay(self, default_delay: float) -> float:
        planned_reconnect_at = self._planned_reconnect_at
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason = None
        if planned_reconnect_at <= 0:
            return default_delay
        return max(default_delay, planned_reconnect_at - time.time())
