"""Command and TTS helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Optional

from .client_state import (
    TTS_MUTE_COMMANDS,
    TTS_SAY_COMMANDS,
    TTS_UNMUTE_COMMANDS,
    TTS_VOLUME_COMMANDS,
    logger,
)
from .hardware import create_hardware
from .tts import create_tts_profile


class ClientCommandsMixin:
    def _start_background_task(self, coro: asyncio.Future[object] | asyncio.coroutines.Coroutine[object, object, object], name: str) -> None:
        task = asyncio.create_task(coro)

        def _log_task_result(done: asyncio.Task[object]) -> None:
            with contextlib.suppress(asyncio.CancelledError):
                exc = done.exception()
                if exc is not None:
                    logger.warning("Background task %s failed: %s", name, exc)

        task.add_done_callback(_log_task_result)

    def _is_motion_command(self, command: str) -> bool:
        return command in {"forward", "backward", "left", "right"}

    def _on_gateway_emergency_stop(self) -> None:
        self._start_background_task(self._trigger_hardware_stop("gateway_emergency_stop"), "gateway_emergency_stop")

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

    def _should_skip_tts_for_chat_message(self, message: str) -> bool:
        return message.lstrip().startswith(".")

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

        if command == "chat":
            if self.config.tts.chat_to_tts and self.tts.can_handle():
                message, tts_metadata = self._normalize_tts_payload(command, value)
                merged_metadata = dict(metadata or {})
                if tts_metadata:
                    merged_metadata.update(tts_metadata)
                if message and not self._should_skip_tts_for_chat_message(message):
                    try:
                        self._tts_queue.put_nowait((message, merged_metadata or None))
                    except asyncio.QueueFull:
                        logger.debug("TTS queue full, dropping message")

        if self._maybe_handle_tts_command(command, value):
            return

        logger.debug("CMD[%s]: %s=%s metadata=%s (latency: %.0fms)", source, command, value, metadata, latency_ms)
        normalized_command = command.strip().lower()
        if normalized_command == "stop":
            self._start_background_task(self._trigger_hardware_stop("stop_command"), "stop_command")
            return

        motion_command_id: int | None = None
        if self._is_motion_command(normalized_command):
            self._latest_motion_command_id += 1
            motion_command_id = self._latest_motion_command_id

        self._start_background_task(
            self._run_hardware_command(
                command,
                value,
                metadata,
                motion_command_id=motion_command_id,
                safety_epoch=self._hardware_safety_epoch,
            ),
            f"hardware_command:{normalized_command}",
        )

    async def _run_hardware_command(
        self,
        command: str,
        value: Any,
        metadata: dict[str, Any] | None,
        motion_command_id: int | None = None,
        safety_epoch: int = 0,
    ) -> None:
        if motion_command_id is not None and motion_command_id < self._latest_motion_command_id:
            return

        async with self._hardware_lock:
            if safety_epoch != self._hardware_safety_epoch:
                return
            if motion_command_id is not None and motion_command_id < self._latest_motion_command_id:
                return
            try:
                await asyncio.to_thread(self.handler.set_command_context, metadata)
                await asyncio.to_thread(self.handler.on_command, command, value)
            except Exception as exc:
                logger.warning("Hardware command error (cmd=%s): %s", command, exc)

    async def _trigger_hardware_stop(self, reason: str) -> None:
        self._hardware_safety_epoch += 1
        self._latest_motion_command_id += 1
        self.stats.last_command_at = 0
        try:
            await asyncio.to_thread(self.handler.emergency_stop)
            logger.debug("Hardware stop applied (%s)", reason)
        except Exception as exc:
            logger.warning("Hardware stop failed (%s): %s", reason, exc)

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

        elif action_type == "update_client":
            logger.info("Remote action: update_client")
            await self._perform_client_update()

        elif action_type == "set_log_stream":
            duration = action.get("durationSec", 120)
            if not isinstance(duration, (int, float)):
                duration = 120
            duration_sec = max(10, min(int(duration), 900))
            self._diag_enabled_until = time.time() + duration_sec
            self._diag_last_sent_idx = 0
            logger.info("Remote action: diagnostics enabled for %ds", duration_sec)
