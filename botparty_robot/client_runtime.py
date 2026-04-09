"""Lifecycle helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Optional

import aiohttp
from livekit import rtc

from .client_state import logger


class ClientLifecycleMixin:
    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, creating it if necessary."""
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def run(self) -> None:
        self._running = True
        while self._running:
            token, robot_id, livekit_url, _ingress_info, publish_tokens = await self._authenticate()
            if not robot_id or not token:
                logger.error("Authentication failed. Retrying in 5s.")
                await asyncio.sleep(5)
                continue

            self._robot_id = robot_id
            self._livekit_publish_token = token
            self._livekit_publish_tokens = publish_tokens
            if livekit_url and livekit_url != self.config.server.livekit_url:
                logger.info("Using LiveKit URL from claim response: %s", livekit_url)
                self.config.server.livekit_url = livekit_url
            logger.info("Authenticated as robot %s", robot_id)

            if self._uses_direct_livekit_publisher():
                await self._connect_direct_livekit()
            else:
                await self._connect(token)

            if not self._running:
                break
            await asyncio.sleep(self._consume_reconnect_delay(default_delay=2.0))

    async def _connect(self, token: str) -> None:
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

    async def shutdown(self) -> None:
        logger.info("Shutting down...")
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

    def _consume_reconnect_delay(self, default_delay: float) -> float:
        planned_reconnect_at = self._planned_reconnect_at
        self._planned_reconnect_at = 0.0
        self._planned_reconnect_reason = None
        if planned_reconnect_at <= 0:
            return default_delay
        return max(default_delay, planned_reconnect_at - time.time())
