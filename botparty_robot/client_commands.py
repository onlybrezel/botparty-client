"""Command and TTS helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol

from .client_contract import ClientComponentBinding
from .client_state import (
    TTS_MUTE_COMMANDS,
    TTS_SAY_COMMANDS,
    TTS_UNMUTE_COMMANDS,
    TTS_VOLUME_COMMANDS,
    QueuedHardwareCommand,
    QueuedTTSCommand,
    logger,
)
from .hardware.common import canonical_command
from .outbox import OutcomeOutboxError
from .protocol import ActionResult, ControlAck, validate_command_value
from .remote_actions import RemoteActionExecutor, RemoteActionHost
from .safety import HardwareCommandCancelled, SafetyController, SafetyLatchedError
from .ws_protocol import WS_EVENTS


@dataclass(frozen=True, slots=True)
class _ValidatedCommand:
    command: str
    normalized_command: str
    value: Any
    latency_ms: float
    is_motion: bool


class CommandsHost(RemoteActionHost, Protocol):
    _gateway: Any
    _hardware_commands: Any
    _hardware_lock: asyncio.Lock
    _hardware_safety_epoch: int
    _latest_motion_command_id: int
    _motion_deadline_generation: int
    _motion_deadline_handle: asyncio.TimerHandle | None
    _outbox_drain_lock: asyncio.Lock
    _outcome_outbox: Any
    _processed_action_ids: Any
    _safety: SafetyController
    _stop_lock: asyncio.Lock
    _stop_worker_task: asyncio.Task[None] | None
    _tts_queue: asyncio.Queue[QueuedTTSCommand]

    def _drain_outcome_outbox(self) -> Coroutine[Any, Any, None]: ...

    def _media_operational(self) -> bool: ...

    def _record_product_milestone(self, name: str) -> None: ...


class ClientCommandsComponent(ClientComponentBinding[CommandsHost]):
    _REMOTE_ACTION_SCOPES: ClassVar[dict[str, str]] = {
        "restart_video": "media:restart",
        "restart_audio": "media:restart",
        "restart_control": "control:restart",
        "reset_safety": "safety:reset",
        "restart_tts": "speak:restart",
        "update_client": "update:install",
        "set_log_stream": "diagnostics:read",
    }

    def _record_fault_if_available(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None:
        recorder = getattr(self.host, "_record_fault", None)
        if callable(recorder):
            recorder(
                code,
                subsystem,
                retryable=retryable,
                safe_detail=safe_detail,
            )

    def _start_background_task(
        self,
        coro: Coroutine[Any, Any, Any],
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
        return self.host.handler.is_motion_command(canonical_command(command))

    def _hardware_supports_command(self, command: str) -> bool:
        supports = getattr(self.host.handler, "supports_command", None)
        return bool(supports and supports(canonical_command(command)))

    def _get_safety_controller(self) -> SafetyController:
        controller = getattr(self.host, "_safety", None)
        if controller is None:
            controller = SafetyController()
            self.host._safety = controller
        return controller

    def _arm_motion_deadline(self) -> None:
        handle = getattr(self.host, "_motion_deadline_handle", None)
        if handle is not None:
            handle.cancel()

        self.host._motion_deadline_generation = (
            getattr(self.host, "_motion_deadline_generation", 0) + 1
        )
        generation = self.host._motion_deadline_generation
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (self.host.config.safety.max_run_time_ms / 1000.0)

        def _expire() -> None:
            if generation != self.host._motion_deadline_generation:
                return
            self.host._start_background_task(
                self.host._trigger_hardware_stop("motion_deadline"),
                "motion_deadline",
            )

        self.host._motion_deadline_handle = loop.call_at(deadline, _expire)

    def _clear_motion_deadline(self) -> None:
        self.host._motion_deadline_generation = (
            getattr(self.host, "_motion_deadline_generation", 0) + 1
        )
        handle = getattr(self.host, "_motion_deadline_handle", None)
        if handle is not None:
            handle.cancel()
        self.host._motion_deadline_handle = None

    def _on_gateway_emergency_stop(self) -> None:
        self.host._start_background_task(
            self.host._trigger_hardware_stop("gateway_emergency_stop"), "gateway_emergency_stop"
        )

    async def _tts_loop(self) -> None:
        while self.host._running:
            try:
                queued = await asyncio.wait_for(self.host._tts_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                if not self.host.tts.should_speak(queued.message, queued.metadata):
                    self._reject_command(
                        queued.metadata,
                        self.host.tts.last_rejection_code or "tts_filtered",
                        "Speech request was filtered",
                    )
                    continue
                if self.host.tts.delay_ms > 0:
                    await asyncio.sleep(self.host.tts.delay_ms / 1000.0)
                    if not self.host.tts.can_handle():
                        self._reject_command(
                            queued.metadata,
                            "tts_disabled",
                            "Speech was disabled before playback",
                        )
                        continue
                await asyncio.wait_for(
                    asyncio.to_thread(self.host.tts.run_say, queued.message, queued.metadata),
                    timeout=self.host.config.tts.operation_timeout_sec,
                )
                self._emit_command_result(queued.metadata, "completed", "tts_played")
            except asyncio.TimeoutError:
                self.host.tts.mute()
                logger.warning("TTS playback exceeded its operation timeout")
                self._reject_command(queued.metadata, "tts_timeout", "Speech operation timed out")
            except Exception as e:
                logger.warning("TTS playback failed: %s", e)
                self._reject_command(queued.metadata, "tts_failed", "Speech operation failed")
            finally:
                self.host._tts_queue.task_done()

    def _maybe_handle_tts_command(
        self,
        command: str,
        value: Any = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        normalized = command.strip().lower()
        if not normalized:
            return False, "not_tts"

        if normalized in TTS_MUTE_COMMANDS:
            self.host.tts.mute()
            while not self.host._tts_queue.empty():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queued = self.host._tts_queue.get_nowait()
                    self._reject_command(
                        queued.metadata,
                        "tts_cancelled",
                        "Speech was cancelled by mute",
                    )
                    self.host._tts_queue.task_done()
            return True, "completed"
        if normalized in TTS_UNMUTE_COMMANDS:
            self.host.tts.unmute()
            return True, "completed"
        if normalized in TTS_VOLUME_COMMANDS:
            level = self._coerce_tts_volume(value)
            if level is not None:
                self.host.tts.set_volume(level)
                return True, "completed"
            return True, "invalid_tts_volume"

        is_tts = normalized in TTS_SAY_COMMANDS or normalized.startswith(
            ("tts:say:", "tts.say:", "say:", "speak:")
        )
        if not is_tts:
            return False, "not_tts"

        message, payload_metadata = self._normalize_tts_payload(command, value)
        metadata = self._merge_tts_metadata(source_metadata, payload_metadata)
        if message:
            try:
                self.host._tts_queue.put_nowait(QueuedTTSCommand(message, metadata or None))
            except asyncio.QueueFull:
                logger.debug("TTS queue full, dropping say command")
                return True, "tts_queue_full"
            return True, "accepted"
        return True, "tts_empty"

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

    def _merge_tts_metadata(
        self,
        source_metadata: dict[str, Any] | None,
        payload_metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        metadata = dict(source_metadata or {})
        if payload_metadata:
            metadata.update(payload_metadata)
        if "sender" not in metadata:
            user_id = metadata.get("userId")
            if isinstance(user_id, str) and user_id.strip():
                metadata["sender"] = user_id.strip()
        return metadata

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
            self.host._start_background_task(
                self._stop_and_ack(metadata),
                "stop_command",
            )
            return

        validated = self._validate_command(command, normalized_command, value, timestamp, metadata)
        if validated is None:
            return

        if validated.is_motion:
            self.host.stats.last_command_at = time.monotonic()
        self.host.stats.commands_received += 1

        if self._handle_chat_command(validated, metadata):
            return
        if self._handle_tts_command(validated.command, validated.value, metadata):
            return
        self._dispatch_hardware_command(validated, metadata, source)

    def _validate_command(
        self,
        command: str,
        normalized_command: str,
        value: Any,
        timestamp: Any,
        metadata: dict[str, Any] | None,
    ) -> _ValidatedCommand | None:
        try:
            ts = float(timestamp)
        except (TypeError, ValueError):
            self._reject_command(metadata, "invalid_timestamp", "A numeric timestamp is required")
            return None
        now_ms = time.time() * 1000
        latency_ms = max(0.0, now_ms - ts)
        receive_samples = getattr(self.host.stats, "command_receive_latency_ms", None)
        if receive_samples is not None:
            receive_samples.append(latency_ms)
        action_id = self._command_action_id(metadata)
        if action_id and action_id in self.host._processed_action_ids:
            self.host.stats.stale_commands += 1
            self._reject_command(metadata, "replayed_action", "Action id was already processed")
            return None
        if latency_ms > self.host.config.safety.command_ttl_ms or ts - now_ms > 5_000:
            self.host.stats.stale_commands += 1
            self._reject_command(metadata, "stale_action", "Command is outside its TTL")
            return None
        if action_id:
            self.host._processed_action_ids.append(action_id)

        is_motion = self._is_motion_command(normalized_command)
        try:
            validated_value = validate_command_value(
                command,
                value,
                is_motion=is_motion,
                hardware_command=True,
            )
        except ValueError:
            self._reject_command(
                metadata,
                "invalid_command_value",
                "Command value does not match its schema",
            )
            return None
        hardware_capabilities = self.host.handler.capabilities()
        if is_motion and hardware_capabilities.support_level != "supported":
            self._reject_command(
                metadata,
                "motion_not_released",
                "Motion is not released for this adapter",
            )
            return None
        if is_motion and not hardware_capabilities.safe_stop:
            self._reject_command(
                metadata,
                "safe_stop_unverified",
                "Motion is disabled until this adapter has verified safe-stop evidence",
            )
            return None
        if is_motion and self.host._get_safety_controller().snapshot().latched:
            self._reject_command(metadata, "safety_latched", "Safety reset is required")
            return None
        if (
            is_motion
            and self.host.config.safety.require_media_for_motion
            and not self.host._media_operational()
        ):
            self._reject_command(metadata, "media_not_ready", "Motion requires operational media")
            return None

        return _ValidatedCommand(
            command=command,
            normalized_command=normalized_command,
            value=validated_value,
            latency_ms=latency_ms,
            is_motion=is_motion,
        )

    def _handle_chat_command(
        self,
        validated: _ValidatedCommand,
        metadata: dict[str, Any] | None,
    ) -> bool:
        if validated.normalized_command != "chat":
            return False

        if self.host.config.tts.chat_to_tts and self.host.tts.can_handle():
            message, payload_metadata = self._normalize_tts_payload(
                validated.command,
                validated.value,
            )
            merged_metadata = self._merge_tts_metadata(metadata, payload_metadata)
            if message and not self._should_skip_tts_for_chat_message(message):
                try:
                    self.host._tts_queue.put_nowait(
                        QueuedTTSCommand(message, merged_metadata or None)
                    )
                except asyncio.QueueFull:
                    logger.debug("TTS queue full, dropping message")
                    self._reject_command(metadata, "tts_queue_full", "Speech queue is full")
                    return True
            else:
                self._emit_command_result(metadata, "completed", "chat_ignored")
            return True

        self._emit_command_result(metadata, "completed", "chat_received")
        return True

    def _handle_tts_command(
        self,
        command: str,
        value: Any,
        metadata: dict[str, Any] | None,
    ) -> bool:
        handled, outcome = self._maybe_handle_tts_command(command, value, metadata)
        if not handled:
            return False
        if outcome == "completed":
            self._emit_command_result(metadata, "completed", "completed")
        elif outcome == "accepted":
            self._emit_command_result(metadata, "accepted", "accepted")
        else:
            self._reject_command(metadata, outcome, "Speech command was rejected")
        return True

    def _dispatch_hardware_command(
        self,
        validated: _ValidatedCommand,
        metadata: dict[str, Any] | None,
        source: str,
    ) -> None:
        if not self._hardware_supports_command(validated.normalized_command):
            self._reject_command(metadata, "unsupported_command", "Command is not supported")
            return

        logger.debug(
            "CMD[%s]: %s=%s metadata=%s (latency: %.0fms)",
            source,
            validated.command,
            validated.value,
            metadata,
            validated.latency_ms,
        )
        motion_command_id: int | None = None
        if validated.is_motion:
            self.host._latest_motion_command_id += 1
            motion_command_id = self.host._latest_motion_command_id
            self._arm_motion_deadline()

        queued = QueuedHardwareCommand(
            validated.command,
            validated.value,
            metadata,
            motion_command_id,
        )
        if self._enqueue_hardware_command(queued):
            self._emit_command_result(metadata, "accepted", "accepted")

    async def _stop_and_ack(self, metadata: dict[str, Any] | None) -> None:
        confirmed = await self.host._trigger_hardware_stop("stop_command")
        if confirmed:
            self._emit_command_result(metadata, "completed", "stopped")
        else:
            self._reject_command(metadata, "stop_unconfirmed", "Hardware stop was not confirmed")

    def _enqueue_hardware_command(self, queued: QueuedHardwareCommand) -> bool:
        previous_drops = self.host._hardware_commands.dropped
        accepted, superseded = self.host._hardware_commands.offer(queued)
        self.host.stats.command_queue_drops += self.host._hardware_commands.dropped - previous_drops
        for command in superseded:
            self._emit_command_result(
                command.metadata,
                "superseded",
                "superseded",
                "A newer motion command replaced this command",
            )
        if not accepted:
            self._reject_command(queued.metadata, "command_queue_full", "Command queue is full")
            return False
        self.host.stats.command_queue_high_watermark = max(
            self.host.stats.command_queue_high_watermark,
            self.host._hardware_commands.high_watermark,
        )
        return True

    async def _hardware_command_loop(self) -> None:
        while self.host._running:
            queued = self.host._hardware_commands.pop_nowait()
            if queued is None:
                await self.host._hardware_commands.wait()
                continue
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
        state: Literal[
            "accepted",
            "completed",
            "rejected",
            "failed",
            "superseded",
            "cancelled_by_stop",
        ],
        code: str,
        detail: str | None = None,
    ) -> None:
        action_id = self._command_action_id(metadata)
        if action_id is None:
            return
        message = None if code in {"accepted", "completed"} else code
        result = ControlAck(
            command_id=action_id,
            status="ACK" if state in {"accepted", "completed"} else "NACK",
            state=state,
            message=message,
        )
        self._queue_outcome(
            kind="command",
            subject_id=action_id,
            state=state,
            event=WS_EVENTS["CONTROL_ACK"],
            data=result.model_dump(by_alias=True),
        )
        if state in {"accepted", "completed"}:
            self.host._record_product_milestone("first_command_ack")
        if state == "completed":
            self.host.stats.last_command_ack_at = time.time()

    async def _run_hardware_command(
        self,
        command: str,
        value: Any,
        metadata: dict[str, Any] | None,
        motion_command_id: int | None = None,
    ) -> None:
        execution_started = time.monotonic()
        if (
            motion_command_id is not None
            and motion_command_id < self.host._latest_motion_command_id
        ):
            self._emit_command_result(metadata, "superseded", "superseded")
            return

        async with self.host._hardware_lock:
            if (
                motion_command_id is not None
                and motion_command_id < self.host._latest_motion_command_id
            ):
                self._emit_command_result(metadata, "superseded", "superseded")
                return
            try:
                if (
                    motion_command_id is not None
                    and self.host.config.safety.require_media_for_motion
                    and not self.host._media_operational()
                ):
                    self._reject_command(
                        metadata,
                        "media_not_ready",
                        "Motion requires an operational media path",
                    )
                    return
                permit = self.host._get_safety_controller().issue_permit()
                await asyncio.to_thread(
                    self.host.handler.execute,
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
            finally:
                execution_samples = getattr(self.host.stats, "command_execution_ms", None)
                if execution_samples is not None:
                    execution_samples.append((time.monotonic() - execution_started) * 1000)

    async def _trigger_hardware_stop(self, reason: str) -> bool:
        stop_started = time.monotonic()
        snapshot = self.host._get_safety_controller().stop(reason)
        self.host._hardware_safety_epoch = snapshot.epoch
        self.host._latest_motion_command_id += 1
        previous_drops = self.host._hardware_commands.dropped
        queued_motion = self.host._hardware_commands.cancel_motion()
        self.host.stats.command_queue_drops += self.host._hardware_commands.dropped - previous_drops
        if queued_motion:
            for command in queued_motion:
                self._emit_command_result(
                    command.metadata,
                    "cancelled_by_stop",
                    "cancelled_by_stop",
                )
        self.host.stats.last_command_at = 0
        self._clear_motion_deadline()
        stop_lock = getattr(self.host, "_stop_lock", None)
        if stop_lock is None:
            stop_lock = asyncio.Lock()
            self.host._stop_lock = stop_lock
        async with stop_lock:
            self.host.stats.last_stop_status = "pending"
            self.host.stats.last_stop_reason = reason
            self.host.stats.last_stop_error_code = None
            self.host.stats.last_stop_at = time.time()
            existing = getattr(self.host, "_stop_worker_task", None)
            if existing is not None and not existing.done():
                self.host.stats.last_stop_status = "failed"
                self.host.stats.last_stop_error_code = "stop_already_pending"
                logger.error("Hardware stop is still pending from an earlier request")
                return False
            worker = asyncio.create_task(asyncio.to_thread(self.host.handler.apply_emergency_stop))
            self.host._stop_worker_task = worker

            def _consume_late_stop_result(done: asyncio.Task[None]) -> None:
                with contextlib.suppress(asyncio.CancelledError):
                    done.exception()

            worker.add_done_callback(_consume_late_stop_result)
            try:
                await asyncio.wait_for(
                    asyncio.shield(worker),
                    timeout=self.host.config.safety.stop_timeout_ms / 1000.0,
                )
            except asyncio.TimeoutError:
                self.host.stats.last_stop_status = "timeout"
                self.host.stats.last_stop_error_code = "stop_timeout"
                logger.error("Hardware stop timed out (%s)", reason)
                self._record_fault_if_available(
                    "safety_stop_timeout",
                    "safety",
                    retryable=False,
                    safe_detail=reason,
                )
                return False
            except Exception as exc:
                self.host.stats.last_stop_status = "failed"
                self.host.stats.last_stop_error_code = "stop_adapter_error"
                logger.error("Hardware stop failed (%s): %s", reason, exc)
                self._record_fault_if_available(
                    "safety_stop_failed",
                    "safety",
                    retryable=False,
                    safe_detail=type(exc).__name__,
                )
                return False

            self.host.stats.last_stop_status = "confirmed"
            stop_samples = getattr(self.host.stats, "stop_confirmation_ms", None)
            if stop_samples is not None:
                stop_samples.append((time.monotonic() - stop_started) * 1000)
            logger.debug("Hardware stop confirmed (%s)", reason)
            self.host.stats.safety_stops += 1
            if reason == "motion_deadline":
                self.host.stats.watchdog_stops += 1
            if reason in {
                "gateway_emergency_stop",
                "gateway_disconnect",
                "control_disconnected",
                "stop_command",
            }:
                self.host.stats.emergency_stops = getattr(self.host.stats, "emergency_stops", 0) + 1
            return True

    async def _execute_action(self, action: dict[str, Any]) -> None:
        executor = getattr(self, "_remote_action_executor", None)
        if executor is None:
            executor = RemoteActionExecutor(self.host, self._REMOTE_ACTION_SCOPES)
            self._remote_action_executor = executor
        await executor.execute(action)

    def _emit_remote_action_result(
        self,
        action_id: str,
        state: Literal["accepted", "completed", "rejected", "failed"],
        code: str,
    ) -> None:
        result = ActionResult(
            action_id=action_id,
            state=state,
            code=code,
            occurred_at_ms=int(time.time() * 1000),
        )
        self._queue_outcome(
            kind="action",
            subject_id=action_id,
            state=state,
            event=WS_EVENTS["ROBOT_ACTION_RESULT"],
            data=result.model_dump(by_alias=True),
        )

    def _queue_outcome(
        self,
        *,
        kind: str,
        subject_id: str,
        state: str,
        event: str,
        data: dict[str, Any],
    ) -> None:
        outbox = getattr(self.host, "_outcome_outbox", None)
        if outbox is None:
            self.host._start_background_task(
                self.host._gateway.send_event(event, data),
                f"outcome:{kind}:{subject_id}:{state}",
            )
            return
        try:
            record = outbox.enqueue(
                kind=kind,
                subject_id=subject_id,
                state=state,
                event=event,
                data=data,
            )
        except OutcomeOutboxError as exc:
            self._record_fault_if_available(
                "outcome_persistence_failed",
                "control",
                retryable=False,
                safe_detail=type(exc).__name__,
            )
            raise RuntimeError("could not persist control outcome") from exc
        if record is not None:
            self.host._start_background_task(self.host._drain_outcome_outbox(), "outcome_outbox")

    async def _drain_outcome_outbox(self) -> None:
        outbox = getattr(self.host, "_outcome_outbox", None)
        lock = getattr(self.host, "_outbox_drain_lock", None)
        if outbox is None or lock is None:
            return
        async with lock:
            for record in outbox.ready_for_delivery():
                if not await self.host._gateway.send_event(record.event, record.data):
                    return
                outbox.mark_attempted(record.outcome_id)

    async def _on_gateway_connected(self) -> None:
        outbox = getattr(self.host, "_outcome_outbox", None)
        if outbox is not None:
            outbox.reset_delivery_attempts()
        await self._drain_outcome_outbox()

    def _on_outcome_ack(self, outcome_id: str) -> None:
        outbox = getattr(self.host, "_outcome_outbox", None)
        if outbox is None:
            return
        try:
            outbox.mark_confirmed(outcome_id)
        except OutcomeOutboxError as exc:
            self._record_fault_if_available(
                "outcome_persistence_failed",
                "control",
                retryable=False,
                safe_detail=type(exc).__name__,
            )


ClientCommandsMixin = ClientCommandsComponent
