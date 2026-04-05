"""Main BotParty robot client - connects to LiveKit and handles commands."""

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
from livekit import rtc

from .camera import CameraManager
from .config import RobotConfig
from .gateway import GatewayConnection
from .hardware import create_hardware
from .tts import create_tts_profile
from .video import create_video_profile

logger = logging.getLogger("botparty.client")

TTS_SAY_COMMANDS = {"say", "speak", "tts", "tts:say", "tts.say"}
TTS_MUTE_COMMANDS = {"tts:mute", "tts.mute", "mute_tts", "tts_mute"}
TTS_UNMUTE_COMMANDS = {"tts:unmute", "tts.unmute", "unmute_tts", "tts_unmute"}
TTS_VOLUME_COMMANDS = {"tts:volume", "tts.volume", "tts_volume", "volume_tts"}


class DiagnosticsBufferHandler(logging.Handler):
    def __init__(self, storage: deque[str], maxlen: int = 400) -> None:
        super().__init__(level=logging.INFO)
        self.storage = storage
        self.maxlen = maxlen

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.storage.append(msg)
            while len(self.storage) > self.maxlen:
                self.storage.popleft()
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


class BotPartyClient:
    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.handler = create_hardware(config)
        self.video_profile = create_video_profile(config)
        self.tts = create_tts_profile(config)
        self._running = False
        self._room: Optional[rtc.Room] = None
        self._robot_id: Optional[str] = None
        self._target_bitrate_kbps: Optional[int] = None
        self._livekit_connected = False

        self._camera = CameraManager(config, self.video_profile)
        self._gateway = GatewayConnection(
            config,
            on_command=self._on_gateway_command,
            on_emergency_stop=self.handler.emergency_stop,
            on_actions=self._apply_remote_actions_payload,
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
        self._tts_queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

        self.stats = WatchdogStats()
        self._diag_enabled_until = 0.0
        self._diag_buffer: deque[str] = deque(maxlen=400)
        self._diag_last_sent_idx = 0
        self._last_heartbeat_stale_warning_at = 0.0
        self._last_telemetry_sent_at = 0.0

        self._diag_handler = DiagnosticsBufferHandler(self._diag_buffer)
        self._diag_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logging.getLogger("botparty").addHandler(self._diag_handler)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
            await asyncio.sleep(2)

    async def _connect(self, token: str) -> None:
        self._room = rtc.Room()

        @self._room.on("data_received")
        def on_data(data: rtc.DataPacket):
            self._handle_data(data)

        @self._room.on("disconnected")
        def on_disconnected():
            logger.warning("Disconnected from LiveKit room")
            if self._running:
                self._livekit_connected = False
                asyncio.create_task(self._stop_media_tasks())

        try:
            await self._room.connect(self.config.server.livekit_url, token)
            self._livekit_connected = True
            logger.info("Connected to LiveKit room: robot-%s", self._robot_id)

            self._camera_task = asyncio.create_task(self._start_camera())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._watchdog_task = asyncio.create_task(self._supervisor())
            self._actions_task = asyncio.create_task(self._actions_loop())
            self._diag_upload_task = asyncio.create_task(self._diagnostics_upload_loop())
            self._tts_task = asyncio.create_task(self._tts_loop())
            self._gateway_task = asyncio.create_task(self._gateway.run())

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
            self._camera_task,
            self._tts_task,
            self._heartbeat_task,
            self._watchdog_task,
            self._actions_task,
            self._diag_upload_task,
            self._gateway_task,
        ]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        if self._room:
            await self._room.disconnect()

        logger.info(
            "Goodbye! Stats: commands=%d frames=%d reconnects=%d",
            self.stats.commands_received,
            self._camera.frame_count,
            self.stats.reconnect_attempts,
        )

    # ------------------------------------------------------------------
    # Camera helpers
    # ------------------------------------------------------------------

    def _start_camera(self) -> Any:
        """Return the camera coroutine with current state bound in."""
        return self._camera.run(
            self._room,
            self._target_bitrate_kbps,
            lambda: self._running,
            lambda: self._livekit_connected,
        )

    async def _stop_media_tasks(self) -> None:
        audio = self._camera.audio_task
        for task in [self._camera_task, audio]:
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    # ------------------------------------------------------------------
    # Supervisor (inspired by remotv watchdog.py)
    # ------------------------------------------------------------------

    async def _supervisor(self) -> None:
        logger.info("Supervisor started")
        timeout_sec = self.config.safety.max_run_time_ms / 1000.0

        while self._running:
            await asyncio.sleep(5)

            # Camera
            if self._camera_task and self._camera_task.done():
                exc = self._camera_task.exception() if not self._camera_task.cancelled() else None
                if exc:
                    logger.error("Camera task died: %s", exc)
                if self._livekit_connected:
                    self.stats.camera_task_restarts += 1
                    if self.stats.camera_task_restarts <= 5:
                        logger.info(
                            "Restarting camera pipeline (attempt %d/5)",
                            self.stats.camera_task_restarts,
                        )
                        self._camera_task = asyncio.create_task(self._start_camera())
                    else:
                        logger.error("Camera restarted 5 times - giving up")

            # Audio
            audio = self._camera.audio_task
            if (
                self._livekit_connected
                and audio
                and audio.done()
                and self.video_profile.has_audio()
            ):
                exc = audio.exception() if not audio.cancelled() else None
                if exc:
                    logger.warning("Audio task died - restarting: %s", exc)
                self._camera.restart_audio(self._room, lambda: self._running)

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
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.config.server.api_url}/api/v1/robots/heartbeat",
                        json={"robotId": self._robot_id},
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status in (200, 201):
                            self.stats.last_heartbeat_at = time.time()

                    if time.time() - self._last_telemetry_sent_at >= 20:
                        await self._send_telemetry(session)
                        self._last_telemetry_sent_at = time.time()
            except Exception as e:
                logger.debug("Heartbeat error (non-fatal): %s", e)
            await asyncio.sleep(15)

    async def _send_telemetry(self, session: aiohttp.ClientSession) -> None:
        payload: dict[str, Any] = {
            "claimToken": self.config.server.claim_token,
            "cpuPercent": None,
            "memoryPercent": None,
            "temperatureC": self._read_temperature_c(),
            "uptimeSec": self._get_uptime_sec(),
            "controlConnected": self._gateway.connected,
            "livekitConnected": self._livekit_connected,
            "commandsReceived": self.stats.commands_received,
            "cameraFrames": self._camera.frame_count,
        }
        try:
            import psutil  # type: ignore
            payload["cpuPercent"] = float(psutil.cpu_percent(interval=None))
            payload["memoryPercent"] = float(psutil.virtual_memory().percent)
            payload["uptimeSec"] = int(time.time() - psutil.boot_time())
        except Exception:
            pass

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
                raw = open(path, "r", encoding="utf-8").read().strip()
                value = float(raw)
                if value > 1000:
                    value /= 1000.0
                if -40 <= value <= 150:
                    return value
            except Exception:
                continue
        return None

    def _get_uptime_sec(self) -> Optional[int]:
        try:
            return int(time.time() - os.times().elapsed)
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

                async with aiohttp.ClientSession() as session:
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
            value = stream.get("targetBitrateKbps")
            next_bitrate: Optional[int] = None
            if isinstance(value, (int, float)) and 150 <= value <= 3000:
                next_bitrate = int(value)
            if next_bitrate != self._target_bitrate_kbps:
                self._target_bitrate_kbps = next_bitrate
                logger.info("Remote stream policy: targetBitrateKbps=%s", self._target_bitrate_kbps)
                if self._camera_task and not self._camera_task.done():
                    self._camera_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._camera_task
                    self._camera_task = asyncio.create_task(self._start_camera())

        for action in payload.get("actions", []) if isinstance(payload, dict) else []:
            if isinstance(action, dict):
                await self._execute_action(action)

    async def _execute_action(self, action: dict) -> None:
        action_type = action.get("type")

        if action_type == "restart_video":
            logger.info("Remote action: restart_video")
            if self._camera_task and not self._camera_task.done():
                self._camera_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(self._camera_task, timeout=2.0)
            self.video_profile = create_video_profile(self.config)
            self._camera.video_profile = self.video_profile
            self._camera_task = asyncio.create_task(self._start_camera())

        elif action_type == "restart_control":
            logger.info("Remote action: restart_control")
            self.handler = create_hardware(self.config)

        elif action_type == "restart_tts":
            logger.info("Remote action: restart_tts")
            self.tts = create_tts_profile(self.config)

        elif action_type == "restart_audio":
            logger.info("Remote action: restart_audio")
            audio = self._camera.audio_task
            if audio and not audio.done():
                audio.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(audio, timeout=2.0)
            if self.video_profile.has_audio() and self._room:
                self._camera.restart_audio(self._room, lambda: self._running)

        elif action_type == "restart_chat":
            logger.info("Remote action: restart_chat (no-op on hardware client)")

        elif action_type == "set_log_stream":
            duration = action.get("durationSec", 120)
            if not isinstance(duration, (int, float)):
                duration = 120
            duration_sec = max(10, min(int(duration), 900))
            self._diag_enabled_until = time.time() + duration_sec
            logger.info("Remote action: diagnostics enabled for %ds", duration_sec)

    # ------------------------------------------------------------------
    # Diagnostics upload
    # ------------------------------------------------------------------

    async def _diagnostics_upload_loop(self) -> None:
        while self._running:
            try:
                if time.time() < self._diag_enabled_until:
                    lines = list(self._diag_buffer)
                    if self._diag_last_sent_idx < len(lines):
                        batch = lines[self._diag_last_sent_idx:self._diag_last_sent_idx + 50]
                        self._diag_last_sent_idx += len(batch)
                        async with aiohttp.ClientSession() as session:
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
            self._tts_queue.put_nowait((message, metadata))
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

    def _handle_data(self, data: rtc.DataPacket) -> None:
        """Handle incoming DataChannel commands from controllers."""
        try:
            payload = json.loads(data.data.decode("utf-8"))
            self._process_command(
                payload.get("command", ""),
                payload.get("value"),
                payload.get("timestamp", 0),
                source="livekit",
            )
        except Exception as e:
            logger.error("Data handling error: %s", e)

    def _on_gateway_command(self, command: str, value: Any, timestamp: Any) -> None:
        self._process_command(command, value, timestamp, source="gateway")

    def _process_command(self, command: str, value: Any, timestamp: Any, source: str) -> None:
        if not command:
            return

        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            ts = time.time() * 1000

        latency_ms = (time.time() * 1000) - ts
        if source == "livekit" and latency_ms > self.config.safety.latency_threshold_ms:
            logger.warning("High latency on %s: %.0fms - triggering E-STOP", source, latency_ms)
            self.handler.emergency_stop()
            return

        if command in {"forward", "backward", "left", "right"}:
            self.stats.last_command_at = time.time()
        self.stats.commands_received += 1

        if self._maybe_handle_tts_command(command, value):
            return

        self.handler.on_command(command, value)
        logger.debug("CMD[%s]: %s=%s (latency: %.0fms)", source, command, value, latency_ms)

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def _authenticate(self) -> tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            async with aiohttp.ClientSession() as session:
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
                    target_kbps = None
                    if isinstance(stream, dict):
                        v = stream.get("targetBitrateKbps")
                        if isinstance(v, (int, float)) and 150 <= v <= 3000:
                            target_kbps = int(v)
                    self._target_bitrate_kbps = target_kbps

                    livekit_url = data.get("livekitUrl")
                    if not isinstance(livekit_url, str):
                        livekit_url = None
                    return data.get("token"), data.get("robotId"), livekit_url
        except Exception as e:
            logger.error("Authentication error: %s", e)
            return None, None, None
