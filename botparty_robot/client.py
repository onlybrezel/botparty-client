"""Main BotParty robot client - connects to LiveKit and handles commands."""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
from livekit import rtc

from . import __version__

from .camera import CameraManager
from .config import RobotConfig, normalize_cameras
from .gateway import GatewayConnection
from .hardware import create_hardware
from .tts import create_tts_profile
from .video import create_video_profile

logger = logging.getLogger("botparty.client")
_SUPPRESS_LIVEKIT_NOISE_UNTIL = 0.0

TTS_SAY_COMMANDS = {"say", "speak", "tts", "tts:say", "tts.say"}
TTS_MUTE_COMMANDS = {"tts:mute", "tts.mute", "mute_tts", "tts_mute"}
TTS_UNMUTE_COMMANDS = {"tts:unmute", "tts.unmute", "unmute_tts", "tts_unmute"}
TTS_VOLUME_COMMANDS = {"tts:volume", "tts.volume", "tts_volume", "volume_tts"}
TELEMETRY_INTERVAL_SEC = 30
GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC = 25.0


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


class BotPartyClient:
    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.handler = create_hardware(config)
        self.tts = create_tts_profile(config)
        self._running = False
        self._room: Optional[rtc.Room] = None
        self._robot_id: Optional[str] = None
        self._configured_target_bitrate_kbps = self._parse_target_bitrate_kbps(
            self.config.video.options.get("target_bitrate_kbps")
        )
        self._remote_target_bitrate_kbps: Optional[int] = None
        self._livekit_connected = False
        self._camera_runtimes = self._build_camera_runtimes()
        self._primary_camera_id = self._resolve_primary_camera_id()
        self._camera = self._camera_runtimes[0].manager if self._camera_runtimes else CameraManager(
            config,
            create_video_profile(config),
        )
        self.video_profile = self._camera_runtimes[0].video_profile if self._camera_runtimes else create_video_profile(config)
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

        # Task references for supervisor
        self._camera_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._actions_task: Optional[asyncio.Task] = None
        self._diag_upload_task: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._gateway_task: Optional[asyncio.Task] = None
        # maxsize prevents unbounded growth when TTS is slow and chat floods in
        self._tts_queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue(maxsize=20)
        # serializes hardware commands so blocking adapters (GPIO time.sleep) don't overlap
        self._hardware_lock: asyncio.Lock = asyncio.Lock()
        # shared HTTP session – created lazily, reused across all REST calls
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if necessary."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    def _build_camera_runtimes(self) -> list[CameraRuntime]:
        normalized = normalize_cameras(self.config)
        runtimes: list[CameraRuntime] = []

        for index, entry in enumerate(normalized):
            if not entry.enabled:
                continue

            derived_config = self.config.model_copy(
                deep=True,
                update={
                    "camera": entry.camera,
                    "video": entry.video,
                },
            )
            video_profile = create_video_profile(derived_config)
            include_audio = index == 0 and video_profile.has_audio()
            track_name = "camera" if len(normalized) == 1 else f"camera.{entry.id}"
            manager = CameraManager(
                derived_config,
                video_profile,
                track_name=track_name,
                audio_enabled=include_audio,
                camera_id=entry.id,
            )
            runtimes.append(
                CameraRuntime(
                    camera_id=entry.id,
                    label=entry.label,
                    role=entry.role,
                    publish_mode=entry.publish_mode,
                    config=derived_config,
                    video_profile=video_profile,
                    manager=manager,
                    include_audio=include_audio,
                )
            )

        return runtimes

    def _resolve_primary_camera_id(self) -> str:
        for runtime in self._camera_runtimes:
            if runtime.role.strip().lower() == "primary":
                return runtime.camera_id
        if self._camera_runtimes:
            return self._camera_runtimes[0].camera_id
        return "front"

    def _sync_primary_runtime_aliases(self) -> None:
        primary = self._get_primary_runtime()
        if primary is None:
            return
        self._primary_camera_id = primary.camera_id
        self._camera = primary.manager
        self.video_profile = primary.video_profile
        self._camera_task = primary.task

    def _get_primary_runtime(self) -> Optional[CameraRuntime]:
        if not self._camera_runtimes:
            return None
        for runtime in self._camera_runtimes:
            if runtime.camera_id == self._primary_camera_id:
                return runtime
        return self._camera_runtimes[0]

    def _total_camera_frames(self) -> int:
        return sum(runtime.manager.frame_count for runtime in self._camera_runtimes)

    async def run(self) -> None:
        self._running = True
        while self._running:
            token, robot_id, livekit_url = await self._authenticate()
            if not token:
                logger.error("Authentication failed. Retrying in 5s.")
                await asyncio.sleep(5)
                continue

            self._robot_id = robot_id
            if livekit_url and livekit_url != self.config.server.livekit_url:
                logger.info("Using LiveKit URL from claim response: %s", livekit_url)
                self.config.server.livekit_url = livekit_url
            logger.info("Authenticated as robot %s", robot_id)

            await self._connect(token)

            if not self._running:
                break
            await asyncio.sleep(self._consume_reconnect_delay(default_delay=2.0))

    async def _connect(self, token: str) -> None:
        self._room = rtc.Room()
        # Reset per-session counters so a fresh connection starts clean.
        self.stats.camera_task_restarts = 0

        @self._room.on("disconnected")
        def on_disconnected():
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
                asyncio.create_task(self._stop_media_tasks())

        try:
            await self._room.connect(self.config.server.livekit_url, token)
            self._livekit_connected = True
            self._planned_disconnect_notice_sent = False
            logger.info("Connected to LiveKit room: robot-%s", self._robot_id)

            await self._start_all_cameras()
            self._ensure_background_tasks()

            while self._running and self._livekit_connected:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error("LiveKit connection error: %s", e)
            self._livekit_connected = False

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
        self._running = False
        self._livekit_connected = False
        self.handler.emergency_stop()

        for task in [
            *(runtime.task for runtime in self._camera_runtimes),
            self._tts_task,
            self._heartbeat_task,
            self._watchdog_task,
            self._actions_task,
            self._diag_upload_task,
            self._gateway_task,
            self._recovery_restart_task,
            self._livekit_reconnect_task,
        ]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        if self._room:
            await self._room.disconnect()

        if self._http_session and not self._http_session.closed:
            await self._http_session.close()

        logger.info(
            "Goodbye! Stats: commands=%d frames=%d reconnects=%d",
            self.stats.commands_received,
            self._total_camera_frames(),
            self.stats.reconnect_attempts,
        )

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------

    def _start_camera(self, runtime: CameraRuntime) -> Any:
        """Return the camera coroutine with current state bound in."""
        return runtime.manager.run(
            self._room,
            self._target_bitrate_for_runtime(runtime),
            lambda: self._running,
            lambda: self._livekit_connected,
        )

    def _parse_target_bitrate_kbps(self, value: Any) -> Optional[int]:
        if isinstance(value, (int, float)) and 150 <= value <= 3000:
            return int(value)
        return None

    def _default_target_bitrate_kbps(self, runtime: CameraRuntime | None = None) -> int:
        active_config = runtime.config if runtime is not None else self.config
        pixels_per_second = active_config.camera.width * active_config.camera.height * max(active_config.camera.fps, 1)
        if pixels_per_second <= 7_500_000:
            return 800
        if pixels_per_second <= 28_000_000:
            return 1500
        return 2200

    def _effective_target_bitrate_kbps(self) -> int:
        return (
            self._remote_target_bitrate_kbps
            or self._configured_target_bitrate_kbps
            or self._default_target_bitrate_kbps()
        )

    def _target_bitrate_for_runtime(self, runtime: CameraRuntime) -> int | None:
        configured = self._parse_target_bitrate_kbps(runtime.config.video.options.get("target_bitrate_kbps"))
        if len(self._camera_runtimes) <= 1 or runtime.camera_id == self._primary_camera_id:
            return self._remote_target_bitrate_kbps or configured or self._default_target_bitrate_kbps(runtime)
        return configured

    async def _start_all_cameras(self) -> None:
        for runtime in self._camera_runtimes:
            runtime.task = asyncio.create_task(self._start_camera(runtime))
        self._sync_primary_runtime_aliases()

    async def _cancel_camera_task(self, runtime: CameraRuntime, timeout_sec: float = 6.5) -> None:
        task = runtime.task
        if not task or task.done():
            return

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout_sec)
        except asyncio.TimeoutError:
            logger.warning(
                "Camera task did not shut down within %.1fs; waiting for device release",
                timeout_sec,
            )
        except asyncio.CancelledError:
            pass

        # Give the OS and ffmpeg a short moment to release /dev/video* cleanly.
        await asyncio.sleep(0.5)
        runtime.task = None
        self._sync_primary_runtime_aliases()

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None:
        async with self._camera_restart_lock:
            if not self._livekit_connected or self._room is None:
                logger.info(
                    "Skipping camera pipeline restart while LiveKit is not ready: %s%s",
                    reason,
                    f" ({camera_id})" if camera_id else "",
                )
                return

            logger.info("Restarting camera pipeline: %s%s", reason, f" ({camera_id})" if camera_id else "")
            targets = (
                [runtime for runtime in self._camera_runtimes if runtime.camera_id == camera_id]
                if camera_id
                else list(self._camera_runtimes)
            )

            for runtime in targets:
                await self._cancel_camera_task(runtime)
                runtime.video_profile = create_video_profile(runtime.config)
                runtime.manager.video_profile = runtime.video_profile
                runtime.task = asyncio.create_task(self._start_camera(runtime))

            self._sync_primary_runtime_aliases()

    async def _stop_media_tasks(self) -> None:
        for runtime in self._camera_runtimes:
            audio = runtime.manager.audio_task
            await self._cancel_camera_task(runtime)
            if audio and not audio.done():
                audio.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await audio

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

    def _consume_reconnect_delay(self, default_delay: float) -> float:
        planned_reconnect_at = self._planned_reconnect_at
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason = None
        if planned_reconnect_at <= 0:
            return default_delay
        return max(default_delay, planned_reconnect_at - time.time())

    async def _handle_gateway_shutdown(
        self,
        reason: str,
        message: str,
        retry_after_sec: float,
        scope: str,
    ) -> None:
        # Give the SDK enough quiet time for the announced outage window,
        # the actual reconnect, and the noisy track republish callbacks after recovery.
        suppress_livekit_reconnect_noise(retry_after_sec + 30.0)
        reconnect_at = time.time() + retry_after_sec
        self._planned_reconnect_at = max(self._planned_reconnect_at, reconnect_at)
        self._planned_reconnect_reason = reason
        self._gateway_outage_scope = scope
        self._livekit_disconnected_during_gateway_outage = False

        if scope != "full" or not self._livekit_connected or self._room is None:
            return

        if self._planned_disconnect_notice_sent:
            return

        self._planned_disconnect_notice_sent = True
        logger.info(
            "Planned %s across full stack; disconnecting LiveKit early to avoid noisy retries",
            reason,
        )

        if self._shutdown_disconnect_task and not self._shutdown_disconnect_task.done():
            return

        self._shutdown_disconnect_task = asyncio.create_task(
            self._disconnect_livekit_for_shutdown(message)
        )

    async def _disconnect_livekit_for_shutdown(self, message: str) -> None:
        room = self._room
        if room is None:
            return

        logger.info("%s", message)
        self._livekit_connected = False
        try:
            await room.disconnect()
        except Exception as exc:
            logger.debug("LiveKit disconnect during planned shutdown failed: %s", exc)

    async def _handle_gateway_disconnected(self, scope: str) -> None:
        if scope != "app":
            return
        if self._gateway_outage_started_at <= 0:
            self._gateway_outage_started_at = time.time()

    async def _handle_gateway_reconnected(self, reason: str, scope: str) -> None:
        outage_started_at = self._gateway_outage_started_at
        outage_scope = self._gateway_outage_scope
        livekit_disconnected = self._livekit_disconnected_during_gateway_outage
        self._gateway_outage_started_at = 0.0
        self._gateway_outage_scope = None
        self._livekit_disconnected_during_gateway_outage = False

        if scope != "app" or outage_scope != "app":
            return

        if not self._livekit_connected:
            return

        if livekit_disconnected:
            logger.info(
                "Control gateway recovered after %s; skipping camera recovery because LiveKit disconnected during the outage",
                reason,
            )
            return

        outage_duration_sec = time.time() - outage_started_at if outage_started_at > 0 else 0.0
        if outage_duration_sec < GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC:
            logger.info(
                "Control gateway recovered after %s in %.1fs; keeping existing camera publish because the stream likely survived",
                reason,
                outage_duration_sec,
            )
            return

        if self._recovery_restart_task and not self._recovery_restart_task.done():
            self._recovery_restart_task.cancel()

        logger.info(
            "Control gateway recovered after %s in %.1fs; scheduling LiveKit room recovery",
            reason,
            outage_duration_sec,
        )
        self._recovery_restart_task = asyncio.create_task(
            self._recover_livekit_room_after_gateway_reconnect(reason)
        )

    async def _recover_livekit_room_after_gateway_reconnect(self, reason: str) -> None:
        try:
            await asyncio.sleep(5)
            if not self._running or not self._livekit_connected or self._room is None:
                logger.info(
                    "Skipping delayed LiveKit recovery after %s because the room is no longer ready",
                    reason,
                )
                return

            if self._livekit_reconnect_task and not self._livekit_reconnect_task.done():
                return

            self._livekit_reconnect_task = asyncio.create_task(
                self._force_livekit_reconnect_after_gateway_recovery(reason)
            )
        except asyncio.CancelledError:
            pass

    async def _force_livekit_reconnect_after_gateway_recovery(self, reason: str) -> None:
        room = self._room
        if room is None or not self._running or not self._livekit_connected:
            return

        logger.info(
            "Forcing LiveKit room reconnect after %s so streams recover cleanly",
            reason,
        )

        self._planned_reconnect_reason = reason
        self._planned_reconnect_at = time.time() + 5
        self._livekit_connected = False

        await self._stop_media_tasks()

        try:
            await asyncio.wait_for(room.disconnect(), timeout=5)
        except asyncio.TimeoutError:
            logger.warning("Timed out while disconnecting LiveKit room during recovery")
        except Exception as exc:
            logger.debug("LiveKit disconnect during recovery failed: %s", exc)

    # ------------------------------------------------------------------
    # Supervisor (inspired by remotv watchdog.py)
    # ------------------------------------------------------------------

    async def _supervisor(self) -> None:
        logger.info("Supervisor started")
        timeout_sec = self.config.safety.max_run_time_ms / 1000.0

        while self._running:
            await asyncio.sleep(5)

            # Cameras + per-camera audio
            for runtime in self._camera_runtimes:
                task = runtime.task
                if task and task.done():
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
                        logger.error("Camera task died (%s): %s", runtime.camera_id, exc)
                    if self._livekit_connected:
                        runtime.restart_count += 1
                        self.stats.camera_task_restarts += 1
                        if runtime.restart_count <= 5:
                            logger.info(
                                "Restarting camera pipeline %s (attempt %d/5)",
                                runtime.camera_id,
                                runtime.restart_count,
                            )
                            await self._restart_camera_pipeline(
                                f"supervisor attempt {runtime.restart_count}/5",
                                camera_id=runtime.camera_id,
                            )
                        else:
                            logger.error("Camera %s restarted 5 times - giving up", runtime.camera_id)

                audio = runtime.manager.audio_task
                if (
                    self._livekit_connected
                    and runtime.include_audio
                    and audio
                    and audio.done()
                    and runtime.video_profile.has_audio()
                ):
                    exc = audio.exception() if not audio.cancelled() else None
                    if exc:
                        logger.warning("Audio task died - restarting (%s): %s", runtime.camera_id, exc)
                    runtime.manager.restart_audio(self._room, lambda: self._running)

            # TTS
            if self._tts_task and self._tts_task.done():
                exc = self._tts_task.exception() if not self._tts_task.cancelled() else None
                if exc:
                    logger.warning("TTS task died - restarting: %s", exc)
                self._tts_task = asyncio.create_task(self._tts_loop())

            # Heartbeat
            if self._heartbeat_task and self._heartbeat_task.done():
                logger.warning("Heartbeat task died - restarting")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # Gateway
            if self._gateway_task and self._gateway_task.done():
                exc = self._gateway_task.exception() if not self._gateway_task.cancelled() else None
                if exc:
                    logger.warning("Gateway task died - restarting: %s", exc)
                self._gateway_task = asyncio.create_task(self._gateway.run())

            # Command timeout safety
            if self.stats.last_command_at > 0:
                elapsed = time.time() - self.stats.last_command_at
                if elapsed > timeout_sec:
                    logger.info("Command timeout (%.0fs) - auto-stopping motors", elapsed)
                    self.handler.emergency_stop()
                    self.stats.last_command_at = 0

            # Heartbeat staleness
            age = time.time() - self.stats.last_heartbeat_at
            if age > 60:
                now = time.time()
                if now - self._last_heartbeat_stale_warning_at >= 30:
                    logger.warning("API heartbeat stale: last success %.0fs ago", age)
                    self._last_heartbeat_stale_warning_at = now

        logger.info("Supervisor stopped")

    # ------------------------------------------------------------------
    # Heartbeat + telemetry
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                sent = await self._gateway.send_event(
                    "robot:heartbeat", {"robotId": self._robot_id}
                )
                if sent:
                    self.stats.last_heartbeat_at = time.time()
                else:
                    # Gateway not connected - fall back to REST
                    session = self._get_session()
                    async with session.post(
                        f"{self.config.server.api_url}/api/v1/robots/heartbeat",
                        json={"robotId": self._robot_id},
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status in (200, 201):
                            self.stats.last_heartbeat_at = time.time()

                if time.time() - self._last_telemetry_sent_at >= TELEMETRY_INTERVAL_SEC:
                    await self._send_telemetry()
                    self._last_telemetry_sent_at = time.time()
            except Exception as e:
                logger.debug("Heartbeat error (non-fatal): %s", e)
            await asyncio.sleep(15)

    async def _send_telemetry(self) -> None:
        payload: dict[str, Any] = {
            "claimToken": self.config.server.claim_token,
            "cpuPercent": self._read_cpu_percent(),
            "memoryPercent": self._read_memory_percent(),
            "temperatureC": self._read_temperature_c(),
            "uptimeSec": self._get_uptime_sec(),
            "controlConnected": self._gateway.connected,
            "livekitConnected": self._livekit_connected,
            "commandsReceived": self.stats.commands_received,
            "cameraFrames": self._total_camera_frames(),
        }
        try:
            import psutil  # type: ignore
            payload["cpuPercent"] = float(psutil.cpu_percent(interval=None))
            payload["memoryPercent"] = float(psutil.virtual_memory().percent)
            boot_time = float(psutil.boot_time())
            payload["uptimeSec"] = max(0, int(time.time() - boot_time))
        except Exception:
            pass

        # WS-first: no REST round-trip overhead when the socket is up
        sent = await self._gateway.send_event("robot:telemetry", payload)
        if not sent:
            session = self._get_session()
            await session.post(
                f"{self.config.server.api_url}/api/v1/robots/telemetry",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=5),
            )

    def _read_temperature_c(self) -> Optional[float]:
        for path in (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        ):
            try:
                with open(path, encoding="utf-8") as fh:
                    value = float(fh.read().strip())
                if value > 1000:
                    value /= 1000.0
                if -40 <= value <= 150:
                    return value
            except Exception:
                continue
        return None

    def _get_uptime_sec(self) -> Optional[int]:
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                return max(0, int(float(fh.read().split()[0])))
        except Exception:
            return None

    def _read_cpu_percent(self) -> Optional[float]:
        try:
            with open("/proc/stat", encoding="utf-8") as fh:
                parts = fh.readline().split()
            if len(parts) < 5 or parts[0] != "cpu":
                return None

            values = [float(value) for value in parts[1:]]
            idle = values[3]
            total = sum(values)
            previous = self._last_cpu_sample
            self._last_cpu_sample = (idle, total)

            if previous is None:
                return None

            prev_idle, prev_total = previous
            total_delta = total - prev_total
            idle_delta = idle - prev_idle
            if total_delta <= 0:
                return None

            usage = 100.0 * (1.0 - (idle_delta / total_delta))
            return max(0.0, min(100.0, usage))
        except Exception:
            return None

    def _read_memory_percent(self) -> Optional[float]:
        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
                for line in handle:
                    key, value = line.split(":", 1)
                    meminfo[key] = int(value.strip().split()[0])

            total = meminfo.get("MemTotal")
            available = meminfo.get("MemAvailable")
            if not total or available is None or total <= 0:
                return None

            used = total - available
            usage = (used / total) * 100.0
            return max(0.0, min(100.0, usage))
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Remote actions
    # ------------------------------------------------------------------

    async def _actions_loop(self) -> None:
        while self._running:
            try:
                if self._gateway.connected:
                    await asyncio.sleep(3)
                    continue

                session = self._get_session()
                async with session.post(
                    f"{self.config.server.api_url}/api/v1/robots/actions/poll",
                    json={"claimToken": self.config.server.claim_token},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        if isinstance(data, dict):
                            await self._apply_remote_actions_payload(data)
            except Exception as e:
                logger.debug("Action poll error (non-fatal): %s", e)
            await asyncio.sleep(3)

    async def _apply_remote_actions_payload(self, payload: dict[str, Any]) -> None:
        stream = payload.get("stream") if isinstance(payload, dict) else None
        if isinstance(stream, dict):
            next_remote_bitrate = self._remote_target_bitrate_kbps
            if "targetBitrateKbps" in stream:
                next_remote_bitrate = self._parse_target_bitrate_kbps(stream.get("targetBitrateKbps"))
            active_camera = stream.get("activeCameraId")
            if isinstance(active_camera, str) and active_camera.strip():
                self._primary_camera_id = active_camera.strip()
                self._sync_primary_runtime_aliases()

            next_effective_bitrate = (
                next_remote_bitrate
                or self._configured_target_bitrate_kbps
                or self._default_target_bitrate_kbps()
            )
            if next_effective_bitrate != self._effective_target_bitrate_kbps() or next_remote_bitrate != self._remote_target_bitrate_kbps:
                self._remote_target_bitrate_kbps = next_remote_bitrate
                logger.info(
                    "Remote stream policy: remoteTargetBitrateKbps=%s effectiveTargetBitrateKbps=%d",
                    self._remote_target_bitrate_kbps,
                    self._effective_target_bitrate_kbps(),
                )
                if self._livekit_connected:
                    await self._restart_camera_pipeline("stream policy updated")

        for action in payload.get("actions", []) if isinstance(payload, dict) else []:
            if isinstance(action, dict):
                await self._execute_action(action)

    async def _execute_action(self, action: dict) -> None:
        action_type = action.get("type")

        if action_type == "restart_video":
            logger.info("Remote action: restart_video")
            if self._livekit_connected:
                await self._restart_camera_pipeline("remote action restart_video")

        elif action_type == "restart_control":
            logger.info("Remote action: restart_control")
            self.handler = create_hardware(self.config)

        elif action_type == "restart_tts":
            logger.info("Remote action: restart_tts")
            self.tts = create_tts_profile(self.config)

        elif action_type == "restart_audio":
            logger.info("Remote action: restart_audio")
            for runtime in self._camera_runtimes:
                if not runtime.include_audio:
                    continue
                audio = runtime.manager.audio_task
                if audio and not audio.done():
                    audio.cancel()
                    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                        await asyncio.wait_for(audio, timeout=2.0)
                if runtime.video_profile.has_audio() and self._room:
                    runtime.manager.restart_audio(self._room, lambda: self._running)

        elif action_type == "restart_chat":
            logger.info("Remote action: restart_chat (no-op on hardware client)")

        elif action_type == "set_log_stream":
            duration = action.get("durationSec", 120)
            if not isinstance(duration, (int, float)):
                duration = 120
            duration_sec = max(10, min(int(duration), 900))
            self._diag_enabled_until = time.time() + duration_sec
            self._diag_last_sent_idx = 0  # restart from current buffer head
            logger.info("Remote action: diagnostics enabled for %ds", duration_sec)

    # ------------------------------------------------------------------
    # Diagnostics upload
    # ------------------------------------------------------------------

    async def _diagnostics_upload_loop(self) -> None:
        while self._running:
            try:
                if time.time() < self._diag_enabled_until:
                    lines = list(self._diag_buffer)
                    # Guard against the deque wrapping: if our stored index now
                    # exceeds the buffer length, reset so new lines are picked up.
                    if self._diag_last_sent_idx >= len(lines):
                        self._diag_last_sent_idx = 0
                    if self._diag_last_sent_idx < len(lines):
                        batch = lines[self._diag_last_sent_idx:self._diag_last_sent_idx + 50]
                        self._diag_last_sent_idx += len(batch)
                        session = self._get_session()
                        await session.post(
                            f"{self.config.server.api_url}/api/v1/robots/logs",
                            json={
                                "claimToken": self.config.server.claim_token,
                                "lines": batch,
                            },
                            headers={"Content-Type": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=5),
                        )
            except Exception as e:
                logger.debug("Diagnostics upload error (non-fatal): %s", e)
            await asyncio.sleep(2)

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------

    async def _tts_loop(self) -> None:
        while self._running:
            try:
                message, metadata = await asyncio.wait_for(self._tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                if not self.tts.should_speak(message, metadata):
                    continue
                if self.tts.delay_ms > 0:
                    await asyncio.sleep(self.tts.delay_ms / 1000.0)
                    if not self.tts.should_speak(message, metadata):
                        continue
                await asyncio.to_thread(self.tts.say, message, metadata)
            except Exception as e:
                logger.warning("TTS playback failed: %s", e)
            finally:
                self._tts_queue.task_done()

    def _maybe_handle_tts_command(self, command: str, value: Any = None) -> bool:
        normalized = command.strip().lower()
        if not normalized:
            return False

        if normalized in TTS_MUTE_COMMANDS:
            self.tts.mute()
            return True
        if normalized in TTS_UNMUTE_COMMANDS:
            self.tts.unmute()
            return True
        if normalized in TTS_VOLUME_COMMANDS:
            level = self._coerce_tts_volume(value)
            if level is not None:
                self.tts.set_volume(level)
            return True

        is_tts = normalized in TTS_SAY_COMMANDS or normalized.startswith(
            ("tts:say:", "tts.say:", "say:", "speak:")
        )
        if not is_tts:
            return False

        message, metadata = self._normalize_tts_payload(command, value)
        if message:
            try:
                self._tts_queue.put_nowait((message, metadata))
            except asyncio.QueueFull:
                logger.debug("TTS queue full, dropping say command")
        return True

    def _normalize_tts_payload(self, command: str, value: Any) -> tuple[str, dict[str, Any] | None]:
        message = ""
        metadata: dict[str, Any] | None = None
        normalized = command.strip()

        for prefix in ("tts:say:", "tts.say:", "say:", "speak:"):
            if normalized.lower().startswith(prefix):
                message = normalized[len(prefix):].strip()
                break

        if not message:
            if isinstance(value, str):
                message = value.strip()
            elif isinstance(value, dict):
                metadata = dict(value)
                for key in ("message", "text", "value"):
                    raw = value.get(key)
                    if isinstance(raw, str):
                        message = raw.strip()
                        break
            elif value is not None:
                message = str(value).strip()

        return message, metadata

    def _coerce_tts_volume(self, value: Any) -> Optional[int]:
        raw = value
        if isinstance(value, dict):
            raw = value.get("level", value.get("value", value.get("volume")))
        try:
            return max(0, min(int(raw), 100))
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    def _on_gateway_command(self, command: str, value: Any, timestamp: Any, metadata: dict[str, Any] | None) -> None:
        self._process_command(command, value, timestamp, source="gateway", metadata=metadata)

    def _process_command(
        self,
        command: str,
        value: Any,
        timestamp: Any,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not command:
            return

        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            ts = time.time() * 1000
        latency_ms = max(0.0, (time.time() * 1000) - ts)

        if command in {"forward", "backward", "left", "right"}:
            self.stats.last_command_at = time.time()
        self.stats.commands_received += 1

        # Chat messages should always reach the hardware adapter.
        # If TTS chat-to-TTS is enabled we additionally enqueue speech.
        if command == "chat":
            if self.config.tts.chat_to_tts and self.tts.can_handle():
                message, tts_metadata = self._normalize_tts_payload(command, value)
                merged_metadata = dict(metadata or {})
                if tts_metadata:
                    merged_metadata.update(tts_metadata)
                if message:
                    try:
                        self._tts_queue.put_nowait((message, merged_metadata or None))
                    except asyncio.QueueFull:
                        logger.debug("TTS queue full, dropping message")

        if self._maybe_handle_tts_command(command, value):
            return

        logger.debug("CMD[%s]: %s=%s metadata=%s (latency: %.0fms)", source, command, value, metadata, latency_ms)
        asyncio.create_task(self._run_hardware_command(command, value, metadata))

    async def _run_hardware_command(self, command: str, value: Any, metadata: dict[str, Any] | None) -> None:
        """Run a hardware command in a thread pool to avoid blocking the event loop.

        The lock serialises commands so GPIO adapters with time.sleep() don't
        overlap - e.g. two simultaneous 'forward' pulses on the same motor pins.
        """
        async with self._hardware_lock:
            try:
                await asyncio.to_thread(self.handler.set_command_context, metadata)
                await asyncio.to_thread(self.handler.on_command, command, value)
            except Exception as exc:
                logger.warning("Hardware command error (cmd=%s): %s", command, exc)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            session = self._get_session()
            async with session.post(
                f"{self.config.server.api_url}/api/v1/robots/claim",
                json={"claimToken": self.config.server.claim_token},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as resp:
                if resp.status in (301, 302, 307, 308):
                    location = resp.headers.get("Location", "")
                    logger.error(
                        "Server redirected (%d) to: %s - check api_url (http vs https)",
                        resp.status,
                        location,
                    )
                    return None, None, None
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error("Claim failed (%d): %s", resp.status, text)
                    if resp.status == 404 and self.config.server.api_url.startswith("http://"):
                        logger.error("Hint: try https:// if your server has SSL enabled")
                    return None, None, None

                data = await resp.json()
                stream = data.get("stream") if isinstance(data, dict) else None
                if isinstance(stream, dict):
                    self._remote_target_bitrate_kbps = self._parse_target_bitrate_kbps(
                        stream.get("targetBitrateKbps")
                    )
                else:
                    self._remote_target_bitrate_kbps = None

                logger.info(
                    "Video target bitrate: remote=%s configured=%s effective=%d kbps",
                    self._remote_target_bitrate_kbps,
                    self._configured_target_bitrate_kbps,
                    self._effective_target_bitrate_kbps(),
                )

                livekit_url = data.get("livekitUrl")
                if not isinstance(livekit_url, str):
                    livekit_url = None
                return data.get("token"), data.get("robotId"), livekit_url
        except Exception as e:
            logger.error("Authentication error: %s", e)
            return None, None, None
