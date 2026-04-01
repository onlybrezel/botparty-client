"""Main BotParty robot client – connects to LiveKit and handles commands."""

import asyncio
import json
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp
from livekit import api as lkapi, rtc

from .config import RobotConfig
from .handlers import DefaultHandler

logger = logging.getLogger("botparty.client")


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
        self.handler = DefaultHandler(config.controls, config.safety)
        self._running = False
        self._room: Optional[rtc.Room] = None
        self._livekit_token: Optional[str] = None
        self._robot_id: Optional[str] = None

        # Task references for supervisor watchdog
        self._camera_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._camera_source: Optional[rtc.VideoSource] = None

        self.stats = WatchdogStats()

    async def run(self) -> None:
        """Main loop: authenticate, connect to LiveKit, run supervisor."""
        self._running = True

        # 1. Authenticate with the API to get LiveKit token
        token, robot_id = await self._authenticate()
        if not token:
            logger.error("Authentication failed. Check your claim_token.")
            return

        self._livekit_token = token
        self._robot_id = robot_id
        logger.info(f"✅ Authenticated as robot {robot_id}")

        # 2. Connect to LiveKit room
        await self._connect(token)

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
                asyncio.create_task(self._reconnect())

        try:
            await self._room.connect(self.config.server.livekit_url, token)
            logger.info(f"🎥 Connected to LiveKit room: robot-{self._robot_id}")

            # Start supervised tasks
            self._camera_task = asyncio.create_task(self._camera_pipeline())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._watchdog_task = asyncio.create_task(self._supervisor_watchdog())

            # Keep running until shutdown
            while self._running:
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"LiveKit connection error: {e}")
        finally:
            await self.shutdown()

    async def _camera_pipeline(self) -> None:
        """Create video source + track, publish to room, start capture loop."""
        try:
            self._camera_source = rtc.VideoSource(
                self.config.camera.width, self.config.camera.height
            )
            track = rtc.LocalVideoTrack.create_video_track("camera", self._camera_source)

            if self._room:
                await self._room.local_participant.publish_track(
                    track,
                    rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA),
                )
                logger.info("📹 Camera track published")

            await self._camera_loop(self._camera_source)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Camera pipeline error: {e}")

    async def _camera_loop(self, source: rtc.VideoSource) -> None:
        """Capture frames from camera and send to LiveKit."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            logger.error("OpenCV/numpy not installed: pip install opencv-python-headless numpy")
            return

        device = self.config.camera.device
        cap = cv2.VideoCapture(0 if device == "/dev/video0" else device)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.camera.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.camera.height)
        cap.set(cv2.CAP_PROP_FPS, self.config.camera.fps)

        if not cap.isOpened():
            logger.error(f"Could not open camera: {device}")
            return

        logger.info(
            f"📷 Camera opened: {device} "
            f"({self.config.camera.width}x{self.config.camera.height}@{self.config.camera.fps}fps)"
        )

        interval = 1.0 / self.config.camera.fps
        consecutive_failures = 0

        while self._running:
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
            lk_frame = rtc.VideoFrame(
                self.config.camera.width,
                self.config.camera.height,
                rtc.VideoBufferType.RGBA,
                frame_rgba.tobytes(),
            )
            source.capture_frame(lk_frame)
            self.stats.camera_frames += 1
            await asyncio.sleep(interval)

        cap.release()
        logger.info("📷 Camera released")

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
                        if resp.status == 200:
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

                self.stats.camera_task_restarts += 1
                if self.stats.camera_task_restarts <= 5:
                    logger.info(
                        f"🔄 Restarting camera pipeline "
                        f"(attempt {self.stats.camera_task_restarts}/5)"
                    )
                    self._camera_task = asyncio.create_task(self._camera_pipeline())
                else:
                    logger.error("Camera restarted 5 times – giving up on camera")

            # ── Heartbeat task supervisor ────────────────────────────────────
            if self._heartbeat_task and self._heartbeat_task.done():
                logger.warning("Heartbeat task died – restarting")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

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

        logger.info("🐕 Supervisor watchdog stopped")

    def _handle_data(self, data: rtc.DataPacket) -> None:
        """Handle incoming DataChannel commands from controllers."""
        try:
            payload = json.loads(data.data.decode("utf-8"))
            command = payload.get("command", "")
            value = payload.get("value")
            timestamp = payload.get("timestamp", 0)

            # Reject commands with no timestamp (safety)
            if not timestamp:
                return

            # Latency check
            latency_ms = (time.time() * 1000) - timestamp
            if latency_ms > self.config.safety.latency_threshold_ms:
                logger.warning(f"⚠️ High latency: {latency_ms:.0f}ms – triggering E-STOP")
                self.handler.emergency_stop()
                return

            self.stats.last_command_at = time.time()
            self.stats.commands_received += 1
            self.handler.on_command(command, value)
            logger.debug(f"CMD: {command}={value} (latency: {latency_ms:.0f}ms)")

        except Exception as e:
            logger.error(f"Data handling error: {e}")

    async def _reconnect(self) -> None:
        """Attempt to reconnect to LiveKit with exponential backoff."""
        if not self._running:
            return

        max_attempts = 10
        for attempt in range(1, max_attempts + 1):
            wait = min(2 ** attempt, 60)
            logger.info(f"🔄 Reconnect attempt {attempt}/{max_attempts} in {wait}s...")
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
                    logger.info(f"✅ Reconnected (attempt {attempt})")

                    # Restart camera pipeline after reconnect
                    if self._camera_task:
                        self._camera_task.cancel()
                    self._camera_task = asyncio.create_task(self._camera_pipeline())
                    return
            except Exception as e:
                logger.error(f"Reconnect attempt {attempt} failed: {e}")

        logger.error("All reconnect attempts exhausted – shutting down")
        await self.shutdown()

    async def _authenticate(self) -> tuple[Optional[str], Optional[str]]:
        """Get LiveKit token from BotParty API using claim token."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.config.server.api_url}/api/v1/robots/claim",
                    json={"claimToken": self.config.server.claim_token},
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status not in (200, 201):
                        text = await resp.text()
                        logger.error(f"Claim failed ({resp.status}): {text}")
                        return None, None
                    data = await resp.json()
                    return data.get("token"), data.get("robotId")
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return None, None

    async def shutdown(self) -> None:
        """Gracefully shut down the client."""
        logger.info("🛑 Shutting down...")
        self._running = False
        self.handler.emergency_stop()

        # Cancel all supervised tasks
        for task_ref in [self._camera_task, self._heartbeat_task, self._watchdog_task]:
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

