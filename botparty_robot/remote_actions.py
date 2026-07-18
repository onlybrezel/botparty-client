"""Remote action state machine with explicit preconditions and postconditions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Coroutine, Mapping
from typing import Any, Literal, Protocol

from .capabilities import build_capability_manifest
from .config import RobotConfig
from .hardware import create_hardware
from .hardware.base import BaseHardware, LoggingHardware
from .safety import SafetyController
from .tts import create_tts_profile
from .tts.base import BaseTTSProfile

logger = logging.getLogger("botparty.client")


class RemoteActionHost(Protocol):
    config: RobotConfig
    handler: BaseHardware
    tts: BaseTTSProfile
    _active_update_action_id: str | None
    _camera_runtimes: list[Any]
    _capability_manifest: dict[str, object]
    _diag_enabled_until: float
    _hardware_safety_epoch: int
    _livekit_connected: bool
    _processed_remote_action_ids: Any
    _room: Any
    _running: bool
    _stop_worker_task: asyncio.Task[None] | None
    _update_in_progress: bool
    _update_manager: Any
    stats: Any

    def _emit_remote_action_result(
        self,
        action_id: str,
        state: Literal["accepted", "completed", "rejected", "failed"],
        code: str,
    ) -> None: ...

    def _get_safety_controller(self) -> SafetyController: ...

    def _start_background_task(self, awaitable: Coroutine[Any, Any, Any], name: str) -> None: ...

    def _total_camera_frames(self) -> int: ...

    async def _perform_client_update(self, action_id: str | None = None) -> None: ...

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None: ...

    async def _trigger_hardware_stop(self, reason: str) -> bool: ...


class RemoteActionExecutor:
    def __init__(self, host: RemoteActionHost, scopes: Mapping[str, str]) -> None:
        self._host = host
        self._scopes = dict(scopes)

    @staticmethod
    def _has_scope(action: dict[str, Any], required: str) -> bool:
        scopes = action.get("scopes")
        return isinstance(scopes, list) and required in {
            str(scope).strip().lower() for scope in scopes
        }

    async def _reset_hardware_stop(self) -> None:
        worker = getattr(self._host, "_stop_worker_task", None)
        if worker is not None and not worker.done():
            raise RuntimeError("safety reset rejected while a stop operation is pending")
        if self._host.stats.last_stop_status not in {"never", "confirmed"}:
            raise RuntimeError("safety reset rejected because the last stop was not confirmed")
        await asyncio.to_thread(self._host.handler.reset_stop)
        snapshot = self._host._get_safety_controller().reset()
        self._host._hardware_safety_epoch = snapshot.epoch
        logger.info("Hardware safety latch reset")

    async def execute(self, action: dict[str, Any]) -> None:
        action_type = action.get("type")
        action_id = action.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            logger.warning("Rejected remote action without actionId")
            return
        if action_id in self._host._processed_remote_action_ids:
            self._host._emit_remote_action_result(action_id, "rejected", "replayed_action")
            return
        self._host._processed_remote_action_ids.append(action_id)
        required_scope = self._scopes.get(str(action_type))
        if required_scope is None or not self._has_scope(action, required_scope):
            logger.warning(
                "Rejected remote action type=%s without scope=%s",
                action_type,
                required_scope or "known-action",
            )
            self._host._emit_remote_action_result(action_id, "rejected", "missing_scope")
            return
        if action_type == "update_client" and self._host._update_manager is None:
            self._host._emit_remote_action_result(action_id, "rejected", "ota_disabled")
            return
        if action_type == "update_client" and self._host._update_in_progress:
            self._host._emit_remote_action_result(action_id, "rejected", "update_in_progress")
            return
        if action_type == "restart_video" and not self._host._livekit_connected:
            self._host._emit_remote_action_result(action_id, "rejected", "not_applicable")
            return
        if action_type == "restart_audio" and not any(
            runtime.include_audio and runtime.video_profile.has_audio()
            for runtime in self._host._camera_runtimes
        ):
            self._host._emit_remote_action_result(action_id, "rejected", "not_applicable")
            return
        if action_type == "update_client":
            self._host._update_in_progress = True
            self._host._active_update_action_id = action_id
        self._host._emit_remote_action_result(action_id, "accepted", "accepted")

        try:
            if action_type == "restart_video":
                await self._restart_video()
            elif action_type == "restart_control":
                await self._restart_control()
            elif action_type == "reset_safety":
                await self._reset_hardware_stop()
            elif action_type == "restart_tts":
                self._host.tts.cancel_active()
                self._host.tts = create_tts_profile(self._host.config)
            elif action_type == "restart_audio":
                await self._restart_audio()
            elif action_type == "update_client":
                self._host._start_background_task(
                    self._host._perform_client_update(action_id), "update_client"
                )
                return
            elif action_type == "set_log_stream":
                if not self._host.config.diagnostics.upload_enabled:
                    self._host._emit_remote_action_result(
                        action_id, "rejected", "diagnostics_disabled"
                    )
                    return
                duration = action.get("durationSec", 120)
                duration_sec = (
                    max(10, min(int(duration), 900)) if isinstance(duration, (int, float)) else 120
                )
                self._host._diag_enabled_until = time.time() + duration_sec
        except Exception as exc:
            logger.warning("Remote action failed type=%s: %s", action_type, exc)
            self._host._emit_remote_action_result(action_id, "failed", "action_failed")
            return
        self._host._emit_remote_action_result(action_id, "completed", "completed")

    async def _restart_video(self) -> None:
        frames_before = self._host._total_camera_frames()
        await self._host._restart_camera_pipeline("remote action restart_video")
        deadline = time.monotonic() + 15.0
        while self._host._total_camera_frames() <= frames_before and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if self._host._total_camera_frames() <= frames_before:
            raise RuntimeError("video restart produced no frame")

    async def _restart_control(self) -> None:
        if not await self._host._trigger_hardware_stop("restart_control"):
            raise RuntimeError("control stop was not confirmed")
        old_handler = self._host.handler
        await asyncio.wait_for(asyncio.to_thread(old_handler.close), timeout=3.0)
        try:
            self._host.handler = create_hardware(self._host.config)
        except Exception as exc:
            self._host.handler = LoggingHardware(self._host.config)
            raise RuntimeError("control adapter failed to start") from exc
        self._host._capability_manifest = build_capability_manifest(
            self._host.config,
            self._host.handler,
            self._host._camera_runtimes,
            self._host.tts,
        )

    async def _restart_audio(self) -> None:
        restarted: list[asyncio.Task[None]] = []
        for runtime in self._host._camera_runtimes:
            if not runtime.include_audio:
                continue
            audio = runtime.manager.audio_task
            if audio and not audio.done():
                audio.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                    await asyncio.wait_for(audio, timeout=2.0)
            if runtime.video_profile.has_audio():
                task = runtime.manager.restart_audio(self._host._room, lambda: self._host._running)
                if task is not None:
                    restarted.append(task)
        await asyncio.sleep(0)
        if not restarted or any(task.done() for task in restarted):
            raise RuntimeError("audio restart did not produce an active task")
