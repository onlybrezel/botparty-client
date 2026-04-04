"""Main BotParty robot client – connects to LiveKit and handles commands."""

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

import aiohttp
from livekit import api as lkapi, rtc

from .config import RobotConfig
from .hardware import create_hardware
from .tts import create_tts_profile
from .video import create_video_profile

logger = logging.getLogger("botparty.client")


CAMERA_BACKEND_MAP = {
    "auto": None,
    "any": None,
    "v4l2": "CAP_V4L2",
    "gstreamer": "CAP_GSTREAMER",
    "ffmpeg": "CAP_FFMPEG",
}

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
    """Runtime health counters surfaced by the watchdog."""
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
        self._livekit_token: Optional[str] = None
        self._robot_id: Optional[str] = None
        self._target_bitrate_kbps: Optional[int] = None

        # Task references for supervisor watchdog
        self._camera_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._actions_task: Optional[asyncio.Task] = None
        self._diag_upload_task: Optional[asyncio.Task] = None
        self._audio_task: Optional[asyncio.Task] = None
        self._tts_task: Optional[asyncio.Task] = None
        self._control_ws_task: Optional[asyncio.Task] = None
        self._camera_source: Optional[rtc.VideoSource] = None
        self._tts_queue: asyncio.Queue[tuple[str, dict[str, Any] | None]] = asyncio.Queue()

        self.stats = WatchdogStats()
        self._diag_enabled_until = 0.0
        self._diag_buffer: deque[str] = deque(maxlen=400)
        self._diag_last_sent_idx = 0
        self._livekit_connected = False
        self._reconnect_task: Optional[asyncio.Task] = None

        self._diag_handler = DiagnosticsBufferHandler(self._diag_buffer)
        self._diag_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logging.getLogger("botparty").addHandler(self._diag_handler)

    async def run(self) -> None:
        """Main loop: authenticate, connect to LiveKit, run supervisor."""
        self._running = True
        while self._running:
            # 1. Authenticate with the API to get LiveKit token
            token, robot_id = await self._authenticate()
            if not token:
                logger.error("Authentication failed. Retrying in 5s.")
                await asyncio.sleep(5)
                continue

            self._livekit_token = token
            self._robot_id = robot_id
            logger.info(f"✅ Authenticated as robot {robot_id}")

            # 2. Connect to LiveKit room
            await self._connect(token)
            if self._running:
                await asyncio.sleep(2)

    async def _connect(self, token: str) -> None:
        """Connect to LiveKit and start all supervised tasks."""
        self._room = rtc.Room()

        @self._room.on("data_received")
        def on_data(data: rtc.DataPacket):
            self._handle_data(data)

        @self._room.on("disconnected")
        def on_disconnected():
            logger.warning("Disconnected from LiveKit room")
            if self._running:
                self._livekit_connected = False
                asyncio.create_task(self._stop_livekit_media_tasks())
                if not self._reconnect_task or self._reconnect_task.done():
                    self._reconnect_task = asyncio.create_task(self._reconnect())

        try:
            await self._room.connect(self.config.server.livekit_url, token)
            self._livekit_connected = True
            logger.info(f"🎥 Connected to LiveKit room: robot-{self._robot_id}")

            # Start supervised tasks
            self._camera_task = asyncio.create_task(self._camera_pipeline())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._watchdog_task = asyncio.create_task(self._supervisor_watchdog())
            self._actions_task = asyncio.create_task(self._actions_loop())
            self._diag_upload_task = asyncio.create_task(self._diagnostics_upload_loop())
            self._tts_task = asyncio.create_task(self._tts_loop())
            self._control_ws_task = asyncio.create_task(self._control_ws_loop())

            # Keep running until shutdown
            while self._running and self._livekit_connected:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"LiveKit connection error: {e}")
            self._livekit_connected = False

    async def _camera_pipeline(self) -> None:
        """Create video source + track, publish to room, start capture loop."""
        cap = None
        try:
            mode = self._camera_pipeline_mode()
            if mode == "none":
                logger.info("📷 Video disabled by profile: %s", self.config.video.type)
                await self.video_profile.run_disabled(lambda: self._running)
                return

            if mode in {"ffmpeg", "sdk"}:
                frame_width = self.config.camera.width
                frame_height = self.config.camera.height
                camera_fps = float(self.config.camera.fps)
            else:
                cap, frame_width, frame_height, camera_fps = self._open_camera()
            self._camera_source = rtc.VideoSource(frame_width, frame_height)
            track = rtc.LocalVideoTrack.create_video_track("camera", self._camera_source)

            if self._room:
                publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA)

                if self._target_bitrate_kbps:
                    try:
                        # Best effort: some SDK versions expose video_encoding on publish options.
                        publish_options.video_encoding = rtc.VideoEncoding(
                            max_bitrate=self._target_bitrate_kbps * 1000,
                            max_framerate=int(round(camera_fps)),
                        )
                        logger.info(
                            "🎚️ Applying target bitrate: "
                            f"{self._target_bitrate_kbps} kbps"
                        )
                    except Exception:
                        logger.warning(
                            "Current LiveKit Python SDK does not expose publish bitrate controls; "
                            "continuing with default encoder settings"
                        )

                await self._room.local_participant.publish_track(track, publish_options)
                logger.info("📹 Camera track published")

            if self.video_profile.has_audio():
                self._audio_task = asyncio.create_task(
                    self.video_profile.start_audio(rtc, self._room, lambda: self._running)
                )

            if mode == "ffmpeg":
                await self._camera_loop_ffmpeg(
                    self._camera_source,
                    frame_width,
                    frame_height,
                    camera_fps,
                )
            elif mode == "sdk":
                await self.video_profile.capture_sdk_frames(
                    rtc,
                    self._camera_source,
                    lambda: self._running,
                    lambda: self._increment_frame_count(),
                )
            else:
                await self._camera_loop(
                    cap,
                    self._camera_source,
                    frame_width,
                    frame_height,
                    camera_fps,
                )

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Camera pipeline error: {e}")
        finally:
            if cap is not None:
                cap.release()
                logger.info("📷 Camera released")
            if self._audio_task is not None and not self._audio_task.done():
                self._audio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._audio_task
                self._audio_task = None

    def _increment_frame_count(self) -> None:
        self.stats.camera_frames += 1

    async def _camera_loop_ffmpeg(
        self,
        source: rtc.VideoSource,
        frame_width: int,
        frame_height: int,
        camera_fps: float,
    ) -> None:
        frame_bytes = frame_width * frame_height * 4
        proc = None
        frames_since_report = 0
        report_started_at = time.monotonic()
        stderr_task = None

        try:
            proc = await self.video_profile.spawn_ffmpeg_process()
            logger.info(
                "📷 Camera opened via %s: device=%s resolution=%dx%d fps=%.1f format=%s",
                self.config.video.type,
                self.config.camera.device,
                frame_width,
                frame_height,
                camera_fps,
                (self.config.camera.fourcc or "auto").upper(),
            )

            if proc.stderr is not None:
                stderr_task = asyncio.create_task(self._drain_ffmpeg_stderr(proc.stderr))

            while self._running and self._livekit_connected:
                if proc.stdout is None:
                    raise RuntimeError("ffmpeg camera process has no stdout pipe")

                frame = await proc.stdout.readexactly(frame_bytes)
                lk_frame = rtc.VideoFrame(
                    frame_width,
                    frame_height,
                    rtc.VideoBufferType.RGBA,
                    frame,
                )
                source.capture_frame(lk_frame)
                self._increment_frame_count()
                frames_since_report += 1

                now = time.monotonic()
                elapsed = now - report_started_at
                if elapsed >= 10:
                    logger.info(
                        "📊 Camera runtime: sent_fps=%.1f target_fps=%.1f resolution=%dx%d",
                        frames_since_report / elapsed,
                        camera_fps,
                        frame_width,
                        frame_height,
                    )
                    frames_since_report = 0
                    report_started_at = now

            if proc.returncode not in (None, 0):
                raise RuntimeError(f"ffmpeg camera process exited with code {proc.returncode}")

        except asyncio.IncompleteReadError as exc:
            raise RuntimeError(
                f"ffmpeg camera stream ended early ({len(exc.partial)} of {frame_bytes} bytes)"
            ) from exc
        except FileNotFoundError as exc:
            raise RuntimeError(
                "ffmpeg is not installed; install it or switch video.type back to 'opencv'"
            ) from exc
        finally:
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
                if proc.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                        await proc.wait()
            if stderr_task is not None:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _drain_ffmpeg_stderr(self, stderr) -> None:
        while True:
            line = await stderr.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                logger.warning("ffmpeg: %s", message)

    async def _camera_loop(
        self,
        cap,
        source: rtc.VideoSource,
        frame_width: int,
        frame_height: int,
        camera_fps: float,
    ) -> None:
        """Capture frames from camera and send to LiveKit."""
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV not installed: pip install opencv-python-headless")
            return

        interval = 1.0 / camera_fps if camera_fps > 0 else 1.0 / max(self.config.camera.fps, 1)
        next_frame_at = time.monotonic()
        consecutive_failures = 0
        frames_since_report = 0
        report_started_at = time.monotonic()

        while self._running and self._livekit_connected:
            ret, frame = cap.read()
            if not ret:
                consecutive_failures += 1
                logger.warning(f"Camera read failure #{consecutive_failures}")
                if consecutive_failures >= 30:
                    logger.error("Camera failed 30 times consecutively – aborting camera loop")
                    break
                await asyncio.sleep(0.1)
                continue

            consecutive_failures = 0
            frame_rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
            frame_rgba = self.video_profile.transform_rgba(frame_rgba, frame_width, frame_height)
            lk_frame = rtc.VideoFrame(
                frame_width,
                frame_height,
                rtc.VideoBufferType.RGBA,
                frame_rgba.tobytes(),
            )
            source.capture_frame(lk_frame)
            self._increment_frame_count()
            frames_since_report += 1

            now = time.monotonic()
            elapsed = now - report_started_at
            if elapsed >= 10:
                logger.info(
                    "📊 Camera runtime: sent_fps=%.1f target_fps=%.1f resolution=%dx%d",
                    frames_since_report / elapsed,
                    camera_fps,
                    frame_width,
                    frame_height,
                )
                frames_since_report = 0
                report_started_at = now

            # Keep a stable cadence: schedule frames on monotonic time instead of
            # sleeping a fixed interval after processing (which reduces real FPS).
            next_frame_at += interval
            sleep_for = next_frame_at - time.monotonic()
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                # If capture/encoding took too long, realign to now to avoid drift.
                next_frame_at = time.monotonic()

    def _open_camera(self):
        import cv2

        device = self._resolve_camera_device()
        backend_flag = self._resolve_camera_backend(cv2)

        cap = (
            cv2.VideoCapture(device, backend_flag)
            if backend_flag is not None
            else cv2.VideoCapture(device)
        )
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera: {self.config.camera.device}")

        self._configure_camera_capture(cap, cv2)

        for _ in range(self.config.camera.warmup_frames):
            with contextlib.suppress(Exception):
                cap.read()

        frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or self.config.camera.width
        frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or self.config.camera.height
        camera_fps = cap.get(cv2.CAP_PROP_FPS)
        if not camera_fps or camera_fps < 1:
            camera_fps = float(self.config.camera.fps)

        backend_name = "default"
        with contextlib.suppress(Exception):
            backend_name = cap.getBackendName() or backend_name

        logger.info(
            "📷 Camera opened: device=%s backend=%s resolution=%dx%d fps=%.1f requested=%dx%d@%dfps",
            self.config.camera.device,
            backend_name,
            frame_width,
            frame_height,
            camera_fps,
            self.config.camera.width,
            self.config.camera.height,
            self.config.camera.fps,
        )
        return cap, frame_width, frame_height, camera_fps

    def _resolve_camera_device(self):
        device = self.config.camera.device.strip()
        match = re.fullmatch(r"/dev/video(\d+)", device)
        if match:
            return int(match.group(1))
        return device

    def _configure_camera_capture(self, cap, cv2) -> None:
        if self.config.camera.fourcc:
            fourcc = self.config.camera.fourcc.strip().upper()
            if len(fourcc) == 4:
                with contextlib.suppress(Exception):
                    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))

        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, self.config.camera.buffer_size)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera.width)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera.height)
        with contextlib.suppress(Exception):
            cap.set(cv2.CAP_PROP_FPS, self.config.camera.fps)

    def _resolve_camera_backend(self, cv2):
        backend = self.config.camera.backend.strip().lower()
        backend_attr = CAMERA_BACKEND_MAP.get(backend)
        if backend_attr is None:
            return None
        return getattr(cv2, backend_attr, None)

    def _camera_pipeline_mode(self) -> str:
        return self.video_profile.capture_mode().strip().lower()

    async def _heartbeat_loop(self) -> None:
        """Send periodic API heartbeat so the server knows we're alive."""
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
            except Exception as e:
                logger.debug(f"Heartbeat error (non-fatal): {e}")
            await asyncio.sleep(15)

    async def _supervisor_watchdog(self) -> None:
        """
        Supervisor loop that monitors all sub-tasks and restarts them if they die.

        Checks every 5 seconds:
        - Camera task: restart if dead and we're still running
        - Command timeout: auto-stop motors if no commands for max_run_time_ms
        - Heartbeat recency: warn if heartbeat is stale
        """
        logger.info("🐕 Supervisor watchdog started")
        timeout_sec = self.config.safety.max_run_time_ms / 1000.0

        while self._running:
            await asyncio.sleep(5)

            # ── Camera task supervisor ───────────────────────────────────────
            if self._camera_task and self._camera_task.done():
                exc = self._camera_task.exception() if not self._camera_task.cancelled() else None
                if exc:
                    logger.error(f"Camera task died with exception: {exc}")
                else:
                    logger.warning("Camera task ended unexpectedly")

                if not self._livekit_connected:
                    continue

                self.stats.camera_task_restarts += 1
                if self.stats.camera_task_restarts <= 5:
                    logger.info(
                        f"🔄 Restarting camera pipeline "
                        f"(attempt {self.stats.camera_task_restarts}/5)"
                    )
                    self._camera_task = asyncio.create_task(self._camera_pipeline())
                else:
                    logger.error("Camera restarted 5 times – giving up on camera")

            if (
                self._livekit_connected
                and self._audio_task
                and self._audio_task.done()
                and self.video_profile.has_audio()
            ):
                exc = self._audio_task.exception() if not self._audio_task.cancelled() else None
                if exc:
                    logger.warning("Audio task died – restarting: %s", exc)
                self._audio_task = asyncio.create_task(
                    self.video_profile.start_audio(rtc, self._room, lambda: self._running)
                )

            if self._tts_task and self._tts_task.done():
                exc = self._tts_task.exception() if not self._tts_task.cancelled() else None
                if exc:
                    logger.warning("TTS task died – restarting: %s", exc)
                self._tts_task = asyncio.create_task(self._tts_loop())

            # ── Heartbeat task supervisor ────────────────────────────────────
            if self._heartbeat_task and self._heartbeat_task.done():
                logger.warning("Heartbeat task died – restarting")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            if self._control_ws_task and self._control_ws_task.done():
                exc = self._control_ws_task.exception() if not self._control_ws_task.cancelled() else None
                if exc:
                    logger.warning("Control websocket task died - restarting: %s", exc)
                self._control_ws_task = asyncio.create_task(self._control_ws_loop())

            # ── Command timeout safety ───────────────────────────────────────
            if self.stats.last_command_at > 0:
                elapsed = time.time() - self.stats.last_command_at
                if elapsed > timeout_sec:
                    logger.info(f"⏱️ Command timeout ({elapsed:.0f}s) – auto-stopping motors")
                    self.handler.emergency_stop()
                    self.stats.last_command_at = 0

            # ── Heartbeat staleness warning ──────────────────────────────────
            heartbeat_age = time.time() - self.stats.last_heartbeat_at
            if heartbeat_age > 60:
                logger.warning(f"⚠️ Heartbeat stale: last sent {heartbeat_age:.0f}s ago")
                if self._livekit_connected:
                    self._livekit_connected = False
                    await self._stop_livekit_media_tasks()
                    if self._room:
                        with contextlib.suppress(Exception):
                            await self._room.disconnect()
                    if not self._reconnect_task or self._reconnect_task.done():
                        self._reconnect_task = asyncio.create_task(self._reconnect())

        logger.info("🐕 Supervisor watchdog stopped")

    async def _actions_loop(self) -> None:
        """Poll pending remote actions from API and execute them."""
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.config.server.api_url}/api/v1/robots/actions/poll",
                        json={"claimToken": self.config.server.claim_token},
                        headers={"Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status in (200, 201):
                            data = await resp.json()
                            stream = data.get("stream") if isinstance(data, dict) else None
                            if isinstance(stream, dict):
                                value = stream.get("targetBitrateKbps")
                                next_bitrate: Optional[int] = None
                                if isinstance(value, (int, float)) and 150 <= value <= 3000:
                                    next_bitrate = int(value)
                                if next_bitrate != self._target_bitrate_kbps:
                                    self._target_bitrate_kbps = next_bitrate
                                    logger.info(
                                        "Remote stream policy updated: targetBitrateKbps=%s",
                                        self._target_bitrate_kbps,
                                    )
                                    if self._camera_task and not self._camera_task.done():
                                        self._camera_task.cancel()
                                        with contextlib.suppress(asyncio.CancelledError):
                                            await self._camera_task
                                        self._camera_task = asyncio.create_task(self._camera_pipeline())

                            actions = data.get("actions", []) if isinstance(data, dict) else []
                            for action in actions:
                                if isinstance(action, dict):
                                    await self._execute_action(action)
            except Exception as e:
                logger.debug(f"Action poll error (non-fatal): {e}")

            await asyncio.sleep(3)

    async def _execute_action(self, action: dict) -> None:
        action_type = action.get("type")
        if action_type == "restart_video":
            logger.info("Remote action: restart_video")
            if self._camera_task and not self._camera_task.done():
                self._camera_task.cancel()
                try:
                    await asyncio.wait_for(self._camera_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.warning("Camera task shutdown timeout - forcing restart anyway")
            self.video_profile = create_video_profile(self.config)
            self._camera_task = asyncio.create_task(self._camera_pipeline())
            return

        if action_type == "restart_control":
            logger.info("Remote action: restart_control")
            self.handler = create_hardware(self.config)
            return

        if action_type == "restart_tts":
            logger.info("Remote action: restart_tts")
            self.tts = create_tts_profile(self.config)
            return

        if action_type == "restart_audio":
            logger.info("Remote action: restart_audio")
            if self._audio_task and not self._audio_task.done():
                self._audio_task.cancel()
                try:
                    await asyncio.wait_for(self._audio_task, timeout=2.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.warning("Audio task shutdown timeout - forcing restart anyway")
            if self.video_profile.has_audio():
                self._audio_task = asyncio.create_task(
                    self.video_profile.start_audio(rtc, self._room, lambda: self._running)
                )
            return

        if action_type == "restart_chat":
            logger.info("Remote action: restart_chat (no-op on hardware client)")
            return

        if action_type == "set_log_stream":
            duration = action.get("durationSec", 120)
            if not isinstance(duration, (int, float)):
                duration = 120
            duration_sec = max(10, min(int(duration), 900))
            self._diag_enabled_until = time.time() + duration_sec
            logger.info(f"Remote action: diagnostics enabled for {duration_sec}s")
            return

    async def _diagnostics_upload_loop(self) -> None:
        """Upload temporary diagnostics logs while diagnostics mode is enabled."""
        while self._running:
            try:
                if time.time() >= self._diag_enabled_until:
                    await asyncio.sleep(2)
                    continue

                lines = list(self._diag_buffer)
                if self._diag_last_sent_idx >= len(lines):
                    await asyncio.sleep(2)
                    continue

                batch = lines[self._diag_last_sent_idx : self._diag_last_sent_idx + 50]
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
                logger.debug(f"Diagnostics upload error (non-fatal): {e}")

            await asyncio.sleep(2)

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

    def _normalize_tts_payload(self, command: str, value: Any) -> tuple[str, dict[str, Any] | None]:
        message = ""
        metadata: dict[str, Any] | None = None
        normalized = command.strip()

        for prefix in ("tts:say:", "tts.say:", "say:", "speak:"):
            if normalized.lower().startswith(prefix):
                message = normalized[len(prefix) :].strip()
                break

        if not message:
            if isinstance(value, str):
                message = value.strip()
            elif isinstance(value, dict):
                metadata = dict(value)
                raw_message = value.get("message")
                if not isinstance(raw_message, str):
                    raw_message = value.get("text")
                if not isinstance(raw_message, str):
                    raw_message = value.get("value")
                if isinstance(raw_message, str):
                    message = raw_message.strip()
            elif value is not None:
                message = str(value).strip()

        return message, metadata

    def _coerce_tts_volume(self, value: Any) -> int | None:
        raw = value
        if isinstance(value, dict):
            raw = value.get("level", value.get("value", value.get("volume")))
        try:
            return max(0, min(int(raw), 100))
        except (TypeError, ValueError):
            return None

    def _maybe_handle_tts_command(self, command: str, value: Any = None) -> bool:
        normalized = command.strip().lower()
        if not normalized:
            return False

        if normalized in TTS_MUTE_COMMANDS:
            self.tts.mute()
            logger.info("TTS muted")
            return True

        if normalized in TTS_UNMUTE_COMMANDS:
            self.tts.unmute()
            logger.info("TTS unmuted")
            return True

        if normalized in TTS_VOLUME_COMMANDS:
            level = self._coerce_tts_volume(value)
            if level is not None:
                self.tts.set_volume(level)
                logger.info("TTS volume set to %s%%", level)
            else:
                logger.warning("Ignoring invalid TTS volume payload: %r", value)
            return True

        is_tts_text_command = normalized in TTS_SAY_COMMANDS or normalized.startswith(
            ("tts:say:", "tts.say:", "say:", "speak:")
        )
        if not is_tts_text_command:
            return False

        message, metadata = self._normalize_tts_payload(command, value)
        if not message:
            logger.debug("Ignoring empty TTS message")
            return True

        self._tts_queue.put_nowait((message, metadata))
        logger.debug("Queued TTS message (%d chars)", len(message))
        return True

    def _handle_data(self, data: rtc.DataPacket) -> None:
        """Handle incoming DataChannel commands from controllers."""
        try:
            payload = json.loads(data.data.decode("utf-8"))
            command = payload.get("command", "")
            value = payload.get("value")
            timestamp = payload.get("timestamp", 0)
            self._process_command(command, value, timestamp, source="livekit")

        except Exception as e:
            logger.error(f"Data handling error: {e}")

    def _process_command(self, command: str, value: Any, timestamp: Any, source: str) -> None:
        if not command:
            return

        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            ts = time.time() * 1000

        latency_ms = (time.time() * 1000) - ts
        if source == "livekit" and latency_ms > self.config.safety.latency_threshold_ms:
            logger.warning(f"⚠️ High latency on {source}: {latency_ms:.0f}ms - triggering E-STOP")
            self.handler.emergency_stop()
            return

        self.stats.last_command_at = time.time()
        self.stats.commands_received += 1

        if self._maybe_handle_tts_command(command, value):
            return

        self.handler.on_command(command, value)
        logger.debug(f"CMD[{source}]: {command}={value} (latency: {latency_ms:.0f}ms)")

    def _resolve_gateway_ws_url(self) -> str:
        api_url = self.config.server.api_url.rstrip("/")
        if api_url.endswith("/api/v1"):
            api_url = api_url[:-7]
        if api_url.startswith("https://"):
            return f"wss://{api_url[len('https://'):]}/ws"
        if api_url.startswith("http://"):
            return f"ws://{api_url[len('http://'):]}/ws"
        return f"ws://{api_url}/ws"

    async def _control_ws_loop(self) -> None:
        """Keep a websocket control channel alive for low-latency command relay."""
        ws_url = self._resolve_gateway_ws_url()
        attempt = 0

        while self._running:
            attempt += 1
            try:
                timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=30)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.ws_connect(ws_url, heartbeat=20) as ws:
                        attempt = 0
                        logger.info("🔌 Control websocket connected")
                        await ws.send_json(
                            {
                                "event": "robot:claim",
                                "data": {"claimToken": self.config.server.claim_token},
                            }
                        )

                        while self._running:
                            try:
                                msg = await ws.receive(timeout=10)
                            except asyncio.TimeoutError:
                                await ws.send_json({"event": "robot:heartbeat", "data": {}})
                                continue

                            if msg.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    payload = json.loads(msg.data)
                                except Exception:
                                    continue

                                event = payload.get("event")
                                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}

                                if event == "control:command":
                                    self._process_command(
                                        str(data.get("command", "")),
                                        data.get("value"),
                                        data.get("timestamp"),
                                        source="gateway",
                                    )
                                elif event == "control:emergency-stop":
                                    logger.warning("Emergency stop from gateway")
                                    self.handler.emergency_stop()
                            elif msg.type == aiohttp.WSMsgType.CLOSED:
                                break
                            elif msg.type == aiohttp.WSMsgType.ERROR:
                                break

                            await ws.send_json({"event": "robot:heartbeat", "data": {}})
            except Exception as e:
                delay = min(2 ** min(attempt, 6), 30)
                logger.warning(f"Control websocket disconnected ({e}); retrying in {delay}s")
                await asyncio.sleep(delay)

    async def _reconnect(self) -> None:
        """Attempt to reconnect to LiveKit with exponential backoff."""
        if not self._running:
            return

        attempt = 0
        while self._running:
            attempt += 1
            wait = min(2 ** attempt, 60)
            logger.info(f"🔄 Reconnect attempt {attempt} in {wait}s...")
            await asyncio.sleep(wait)

            if not self._running:
                return

            try:
                # Refresh token before reconnect
                token, robot_id = await self._authenticate()
                if not token:
                    logger.error("Re-authentication failed")
                    continue

                self._livekit_token = token
                self.stats.reconnect_attempts += 1

                if self._room:
                    await self._room.connect(self.config.server.livekit_url, token)
                    self._livekit_connected = True
                    logger.info(f"✅ Reconnected (attempt {attempt})")

                    # Restart camera pipeline after reconnect
                    if self._camera_task:
                        self._camera_task.cancel()
                    self._camera_task = asyncio.create_task(self._camera_pipeline())
                    return
            except Exception as e:
                logger.error(f"Reconnect attempt {attempt} failed: {e}")

        self._livekit_connected = False

    async def _stop_livekit_media_tasks(self) -> None:
        """Stop camera/audio tasks when LiveKit disconnects so local capture does not keep running."""
        for task_ref in [self._camera_task, self._audio_task]:
            if task_ref and not task_ref.done():
                task_ref.cancel()
                try:
                    await task_ref
                except (asyncio.CancelledError, Exception):
                    pass

    async def _authenticate(self) -> tuple[Optional[str], Optional[str]]:
        """Get LiveKit token from BotParty API using claim token."""
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
                            f"Server redirected ({resp.status}) to: {location}. "
                            "Your api_url is likely http:// but the server requires https://. "
                            "Update api_url in your config.yaml."
                        )
                        return None, None
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        logger.error(f"Claim failed ({resp.status}): {text}")
                        if resp.status == 404 and self.config.server.api_url.startswith("http://"):
                            logger.error(
                                "Hint: your api_url uses http:// - "
                                "try https:// if your server has SSL enabled."
                            )
                        return None, None
                    data = await resp.json()

                    stream = data.get("stream") if isinstance(data, dict) else None
                    target_kbps = None
                    if isinstance(stream, dict):
                        value = stream.get("targetBitrateKbps")
                        if isinstance(value, (int, float)) and 150 <= value <= 3000:
                            target_kbps = int(value)

                    self._target_bitrate_kbps = target_kbps
                    return data.get("token"), data.get("robotId")
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return None, None

    async def shutdown(self) -> None:
        """Gracefully shut down the client."""
        logger.info("🛑 Shutting down...")
        self._running = False
        self._livekit_connected = False
        self.handler.emergency_stop()

        # Cancel all supervised tasks
        for task_ref in [
            self._camera_task,
            self._audio_task,
            self._tts_task,
            self._heartbeat_task,
            self._watchdog_task,
            self._actions_task,
            self._diag_upload_task,
            self._control_ws_task,
            self._reconnect_task,
        ]:
            if task_ref and not task_ref.done():
                task_ref.cancel()
                try:
                    await task_ref
                except (asyncio.CancelledError, Exception):
                    pass

        if self._room:
            await self._room.disconnect()

        logger.info(
            f"👋 Goodbye! Stats: commands={self.stats.commands_received}, "
            f"frames={self.stats.camera_frames}, reconnects={self.stats.reconnect_attempts}"
        )
