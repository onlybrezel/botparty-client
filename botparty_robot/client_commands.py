"""Command and TTS helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable
from typing import Any, ClassVar

from .capabilities import build_capability_manifest
from .client_state import (
    TTS_MUTE_COMMANDS,
    TTS_SAY_COMMANDS,
    TTS_UNMUTE_COMMANDS,
    TTS_VOLUME_COMMANDS,
    QueuedHardwareCommand,
    logger,
)
from .hardware import create_hardware
from .hardware.base import LoggingHardware
from .hardware.common import canonical_command
from .protocol import ActionResult, ControlAck
from .safety import HardwareCommandCancelled, SafetyController, SafetyLatchedError
from .tts import create_tts_profile
from .tts.common import terminate_active_tts_processes
from .ws_protocol import WS_EVENTS


class ClientCommandsMixin:
    _REMOTE_ACTION_SCOPES: ClassVar[dict[str, str]] = {
        "restart_video": "media:restart",
        "restart_audio": "media:restart",
        "restart_control": "control:restart",
        "reset_safety": "safety:reset",
        "restart_tts": "speak:restart",
        "restart_chat": "speak:restart",
        "update_client": "update:install",
        "set_log_stream": "diagnostics:read",
    }

    def _start_background_task(
        self,
        coro: Awaitable[object],
        name: str,
    ) -> None:
        task = asyncio.create_task(coro)

        def _log_task_result(done: asyncio.Task[object]) -> None:
            with contextlib.suppress(asyncio.CancelledError):
                exc = done.exception()
                if exc is not None:
                    logger.warning("Background task %s failed: %s", name, exc)

        task.add_done_callback(_log_task_result)

    def _is_motion_command(self, command: str) -> bool:
        return self.handler.is_motion_command(canonical_command(command))

    def _get_safety_controller(self) -> SafetyController:
        controller = getattr(self, "_safety", None)
        if controller is None:
            controller = SafetyController()
            self._safety = controller
        return controller

    def _arm_motion_deadline(self) -> None:
        handle = getattr(self, "_motion_deadline_handle", None)
        if handle is not None:
            handle.cancel()

        self._motion_deadline_generation = getattr(self, "_motion_deadline_generation", 0) + 1
        generation = self._motion_deadline_generation
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (self.config.safety.max_run_time_ms / 1000.0)

        def _expire() -> None:
            if generation != self._motion_deadline_generation:
                return
            self._start_background_task(
                self._trigger_hardware_stop("motion_deadline"),
                "motion_deadline",
            )

        self._motion_deadline_handle = loop.call_at(deadline, _expire)

    def _clear_motion_deadline(self) -> None:
        self._motion_deadline_generation = getattr(self, "_motion_deadline_generation", 0) + 1
        handle = getattr(self, "_motion_deadline_handle", None)
        if handle is not None:
            handle.cancel()
        self._motion_deadline_handle = None

    def _on_gateway_emergency_stop(self) -> None:
        self._start_background_task(
            self._trigger_hardware_stop("gateway_emergency_stop"), "gateway_emergency_stop"
        )

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
                    if not self.tts.can_handle():
                        continue
                await asyncio.wait_for(
                    asyncio.to_thread(self.tts.say, message, metadata),
                    timeout=self.config.tts.operation_timeout_sec,
                )
            except asyncio.TimeoutError:
                terminate_active_tts_processes()
                logger.warning("TTS playback exceeded its operation timeout")
            except Exception as e:
                logger.warning("TTS playback failed: %s", e)
            finally:
                self._tts_queue.task_done()

    def _maybe_handle_tts_command(
        self,
        command: str,
        value: Any = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> bool:
        normalized = command.strip().lower()
        if not normalized:
            return False

        if normalized in TTS_MUTE_COMMANDS:
            self.tts.mute()
            while not self._tts_queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    self._tts_queue.get_nowait()
                    self._tts_queue.task_done()
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

        message, payload_metadata = self._normalize_tts_payload(command, value)
        metadata = dict(source_metadata or {})
        if payload_metadata:
            metadata.update(payload_metadata)
        if "sender" not in metadata:
            user_id = metadata.get("userId")
            if isinstance(user_id, str) and user_id.strip():
                metadata["sender"] = user_id.strip()
        if message:
            try:
                self._tts_queue.put_nowait((message, metadata or None))
            except asyncio.QueueFull:
                logger.debug("TTS queue full, dropping say command")
        return True

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
                for key in ("message", "text", "value"):
                    raw = value.get(key)
                    if isinstance(raw, str):
                        message = raw.strip()
                        break
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

    def _should_skip_tts_for_chat_message(self, message: str) -> bool:
        return message.lstrip().startswith(".")

    def _on_gateway_command(
        self, command: str, value: Any, timestamp: Any, metadata: dict[str, Any] | None
    ) -> None:
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

        normalized_command = canonical_command(command)
        if normalized_command == "stop":
            self._start_background_task(
                self._stop_and_ack(metadata),
                "stop_command",
            )
            return

        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            self._reject_command(metadata, "invalid_timestamp", "A numeric timestamp is required")
            return
        now_ms = time.time() * 1000
        latency_ms = max(0.0, now_ms - ts)
        action_id = self._command_action_id(metadata)
        if action_id and action_id in self._processed_action_ids:
            self.stats.stale_commands += 1
            self._reject_command(metadata, "replayed_action", "Action id was already processed")
            return
        if latency_ms > self.config.safety.command_ttl_ms or ts - now_ms > 5_000:
            self.stats.stale_commands += 1
            self._reject_command(metadata, "stale_action", "Command is outside its TTL")
            return
        if action_id:
            self._processed_action_ids.append(action_id)

        is_motion = self._is_motion_command(normalized_command)
        if is_motion and self._get_safety_controller().snapshot().latched:
            self._reject_command(metadata, "safety_latched", "Safety reset is required")
            return
        if (
            is_motion
            and self.config.safety.require_media_for_motion
            and not self._media_operational()
        ):
            self._reject_command(metadata, "media_not_ready", "Motion requires operational media")
            return

        if is_motion:
            self.stats.last_command_at = time.monotonic()
        self.stats.commands_received += 1

        if command == "chat" and self.config.tts.chat_to_tts and self.tts.can_handle():
            message, tts_metadata = self._normalize_tts_payload(command, value)
            merged_metadata = dict(metadata or {})
            if tts_metadata:
                merged_metadata.update(tts_metadata)
            if "sender" not in merged_metadata:
                user_id = merged_metadata.get("userId")
                if isinstance(user_id, str) and user_id.strip():
                    merged_metadata["sender"] = user_id.strip()
            if message and not self._should_skip_tts_for_chat_message(message):
                try:
                    self._tts_queue.put_nowait((message, merged_metadata or None))
                except asyncio.QueueFull:
                    logger.debug("TTS queue full, dropping message")

        if self._maybe_handle_tts_command(command, value, metadata):
            self._emit_command_result(metadata, "completed", "completed")
            return

        logger.debug(
            "CMD[%s]: %s=%s metadata=%s (latency: %.0fms)",
            source,
            command,
            value,
            metadata,
            latency_ms,
        )
        motion_command_id: int | None = None
        if is_motion:
            self._latest_motion_command_id += 1
            motion_command_id = self._latest_motion_command_id
            self._arm_motion_deadline()

        queued = QueuedHardwareCommand(command, value, metadata, motion_command_id)
        if self._enqueue_hardware_command(queued):
            self._emit_command_result(metadata, "accepted", "accepted")

    async def _stop_and_ack(self, metadata: dict[str, Any] | None) -> None:
        await self._trigger_hardware_stop("stop_command")
        self._emit_command_result(metadata, "completed", "stopped")

    def _enqueue_hardware_command(self, queued: QueuedHardwareCommand) -> bool:
        if queued.motion_command_id is not None:
            retained = [
                command for command in self._command_queue if command.motion_command_id is None
            ]
            dropped = len(self._command_queue) - len(retained)
            if dropped:
                self._command_queue.clear()
                self._command_queue.extend(retained)
                self.stats.command_queue_drops += dropped
        if len(self._command_queue) >= self._command_queue.maxlen:
            self.stats.command_queue_drops += 1
            self._reject_command(queued.metadata, "command_queue_full", "Command queue is full")
            return False
        self._command_queue.append(queued)
        self.stats.command_queue_high_watermark = max(
            self.stats.command_queue_high_watermark,
            len(self._command_queue),
        )
        self._command_queue_event.set()
        return True

    async def _hardware_command_loop(self) -> None:
        while self._running:
            if not self._command_queue:
                self._command_queue_event.clear()
                await self._command_queue_event.wait()
                continue
            queued = self._command_queue.popleft()
            await self._run_hardware_command(
                queued.command,
                queued.value,
                queued.metadata,
                queued.motion_command_id,
            )

    def _command_action_id(self, metadata: dict[str, Any] | None) -> str | None:
        if not metadata:
            return None
        value = metadata.get("actionId")
        return value.strip() if isinstance(value, str) and value.strip() else None

    def _reject_command(
        self,
        metadata: dict[str, Any] | None,
        code: str,
        detail: str,
    ) -> None:
        logger.info("Command rejected (%s): %s", code, detail)
        self._emit_command_result(metadata, "rejected", code, detail)

    def _emit_command_result(
        self,
        metadata: dict[str, Any] | None,
        state: str,
        code: str,
        detail: str | None = None,
    ) -> None:
        action_id = self._command_action_id(metadata)
        if action_id is None:
            return
        if state == "accepted":
            return
        message = None if code in {"accepted", "completed"} else code
        result = ControlAck(
            command_id=action_id,
            status="ACK" if state in {"accepted", "completed"} else "NACK",
            message=message,
        )
        self._start_background_task(
            self._gateway.send_event(
                WS_EVENTS["CONTROL_ACK"],
                result.model_dump(by_alias=True),
            ),
            f"command_result:{action_id}",
        )
        if state in {"accepted", "completed"}:
            self._record_product_milestone("first_command_ack")
        if state == "completed":
            self.stats.last_command_ack_at = time.time()

    async def _run_hardware_command(
        self,
        command: str,
        value: Any,
        metadata: dict[str, Any] | None,
        motion_command_id: int | None = None,
    ) -> None:
        if motion_command_id is not None and motion_command_id < self._latest_motion_command_id:
            return

        async with self._hardware_lock:
            if motion_command_id is not None and motion_command_id < self._latest_motion_command_id:
                return
            try:
                if (
                    motion_command_id is not None
                    and self.config.safety.require_media_for_motion
                    and not self._media_operational()
                ):
                    self._reject_command(
                        metadata,
                        "media_not_ready",
                        "Motion requires an operational media path",
                    )
                    return
                permit = self._get_safety_controller().issue_permit()
                await asyncio.to_thread(
                    self.handler.execute,
                    permit,
                    command,
                    value,
                    metadata,
                )
                self._emit_command_result(metadata, "completed", "completed")
            except SafetyLatchedError as exc:
                logger.info("Hardware command rejected while stopped (cmd=%s): %s", command, exc)
                self._reject_command(metadata, "safety_latched", "Safety reset is required")
            except HardwareCommandCancelled:
                logger.debug("Hardware command cancelled by safety latch (cmd=%s)", command)
                self._reject_command(metadata, "cancelled_by_stop", "Command was stopped")
            except Exception as exc:
                logger.warning("Hardware command error (cmd=%s): %s", command, exc)
                self._reject_command(metadata, "hardware_error", "Hardware command failed")

    async def _trigger_hardware_stop(self, reason: str) -> None:
        snapshot = self._get_safety_controller().stop(reason)
        self._hardware_safety_epoch = snapshot.epoch
        self._latest_motion_command_id += 1
        self.stats.last_command_at = 0
        self._clear_motion_deadline()
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self.handler.apply_emergency_stop),
                timeout=self.config.safety.stop_timeout_ms / 1000.0,
            )
            logger.debug("Hardware stop applied (%s)", reason)
            if hasattr(self.stats, "safety_stops"):
                self.stats.safety_stops += 1
            if reason == "motion_deadline" and hasattr(self.stats, "watchdog_stops"):
                self.stats.watchdog_stops += 1
            if reason in {
                "gateway_emergency_stop",
                "gateway_disconnect",
                "control_disconnected",
                "stop_command",
            } and hasattr(self.stats, "emergency_stops"):
                self.stats.emergency_stops += 1
        except Exception as exc:
            logger.warning("Hardware stop failed (%s): %s", reason, exc)

    async def _reset_hardware_stop(self) -> None:
        await asyncio.to_thread(self.handler.reset_stop)
        snapshot = self._get_safety_controller().reset()
        self._hardware_safety_epoch = snapshot.epoch
        logger.info("Hardware safety latch reset")

    def _action_has_scope(self, action: dict[str, Any], required: str) -> bool:
        scopes = action.get("scopes")
        return isinstance(scopes, list) and required in {
            str(scope).strip().lower() for scope in scopes
        }

    async def _execute_action(self, action: dict) -> None:
        action_type = action.get("type")
        action_id = action.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            logger.warning("Rejected remote action without actionId")
            return
        if action_id in self._processed_remote_action_ids:
            self._emit_remote_action_result(
                action_id,
                "rejected",
                "replayed_action",
            )
            return
        self._processed_remote_action_ids.append(action_id)
        required_scope = self._REMOTE_ACTION_SCOPES.get(str(action_type))
        if required_scope is None or not self._action_has_scope(action, required_scope):
            logger.warning(
                "Rejected remote action type=%s without scope=%s",
                action_type,
                required_scope or "known-action",
            )
            self._emit_remote_action_result(action_id, "rejected", "missing_scope")
            return
        self._emit_remote_action_result(action_id, "accepted", "accepted")

        try:
            if action_type == "restart_video":
                logger.info("Remote action: restart_video")
                if self._livekit_connected:
                    await self._restart_camera_pipeline("remote action restart_video")

            elif action_type == "restart_control":
                logger.info("Remote action: restart_control")
                await self._trigger_hardware_stop("restart_control")
                old_handler = self.handler
                await asyncio.wait_for(asyncio.to_thread(old_handler.close), timeout=3.0)
                try:
                    self.handler = create_hardware(self.config)
                except Exception as exc:
                    logger.error("Hardware restart failed; using disabled adapter: %s", exc)
                    self.handler = LoggingHardware(self.config)
                self._capability_manifest = build_capability_manifest(
                    self.config,
                    self.handler,
                    self._camera_runtimes,
                    self.tts,
                )

            elif action_type == "reset_safety":
                await self._reset_hardware_stop()

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
                logger.info("Remote action: restart_chat")

            elif action_type == "update_client":
                logger.info("Remote action: update_client")
                self._start_background_task(
                    self._perform_client_update(action_id),
                    "update_client",
                )
                return

            elif action_type == "set_log_stream":
                if not self.config.diagnostics.upload_enabled:
                    logger.warning("Rejected diagnostics action because upload is disabled")
                    self._emit_remote_action_result(
                        action_id,
                        "rejected",
                        "diagnostics_disabled",
                    )
                    return
                duration = action.get("durationSec", 120)
                if not isinstance(duration, (int, float)):
                    duration = 120
                duration_sec = max(10, min(int(duration), 900))
                self._diag_enabled_until = time.time() + duration_sec
                logger.info("Remote action: diagnostics enabled for %ds", duration_sec)
        except Exception as exc:
            logger.warning("Remote action failed type=%s: %s", action_type, exc)
            self._emit_remote_action_result(action_id, "failed", "action_failed")
            return
        self._emit_remote_action_result(action_id, "completed", "completed")

    def _emit_remote_action_result(
        self,
        action_id: str,
        state: str,
        code: str,
    ) -> None:
        result = ActionResult(
            action_id=action_id,
            state=state,
            code=code,
            occurred_at_ms=int(time.time() * 1000),
        )
        self._start_background_task(
            self._gateway.send_event(
                WS_EVENTS["ROBOT_ACTION_RESULT"],
                result.model_dump(by_alias=True),
            ),
            f"remote_action_result:{action_id}:{state}",
        )
