"""Media and reconnect helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Optional

from .camera import CameraManager
from .client_state import (
    CameraRuntime,
    GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC,
    logger,
    suppress_livekit_reconnect_noise,
)
from .config import normalize_cameras
from .publisher import LiveKitPublisherManager
from .video import create_video_profile


class ClientMediaMixin:
    def _uses_direct_livekit_publisher(self) -> bool:
        return bool(self._camera_runtimes) and all(
            runtime.video_profile.publish_transport() == "livekit_direct"
            for runtime in self._camera_runtimes
        )

    def _uses_external_media_transport(self) -> bool:
        return self._uses_direct_livekit_publisher()

    def _validate_media_mode(self) -> None:
        transports = {runtime.video_profile.publish_transport() for runtime in self._camera_runtimes}
        if len(transports) > 1:
            raise ValueError("External and legacy LiveKit camera profiles cannot be mixed in one client config")

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
            include_audio = (
                index == 0
                and video_profile.has_audio()
            )
            track_name = "camera" if len(normalized) == 1 else f"camera.{entry.id}"
            if video_profile.publish_transport() == "livekit_direct":
                manager = LiveKitPublisherManager(
                    derived_config,
                    video_profile,
                    token_fn=lambda camera_id=entry.id: (
                        self._livekit_publish_tokens.get(camera_id) or self._livekit_publish_token
                    ),
                    audio_token_fn=lambda: self._livekit_publish_token,
                    livekit_url_fn=lambda: self.config.server.livekit_url,
                    camera_id=entry.id,
                    audio_enabled=include_audio,
                )
            else:
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

    def _start_camera(self, runtime: CameraRuntime) -> Any:
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

    def _resolve_target_bitrate_kbps(
        self,
        *,
        remote: Optional[int],
        configured: Optional[int],
        default: int,
    ) -> int:
        if remote is not None and configured is not None:
            return max(remote, configured)
        return remote or configured or default

    def _effective_target_bitrate_kbps(self) -> int:
        return self._resolve_target_bitrate_kbps(
            remote=self._remote_target_bitrate_kbps,
            configured=self._configured_target_bitrate_kbps,
            default=self._default_target_bitrate_kbps(),
        )

    def _target_bitrate_for_runtime(self, runtime: CameraRuntime) -> int | None:
        configured = self._parse_target_bitrate_kbps(runtime.config.video.options.get("target_bitrate_kbps"))
        if len(self._camera_runtimes) <= 1 or runtime.camera_id == self._primary_camera_id:
            return self._resolve_target_bitrate_kbps(
                remote=self._remote_target_bitrate_kbps,
                configured=configured,
                default=self._default_target_bitrate_kbps(runtime),
            )
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

        await asyncio.sleep(0.5)
        runtime.task = None
        self._sync_primary_runtime_aliases()

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None:
        async with self._camera_restart_lock:
            if not self._livekit_connected or (not self._uses_external_media_transport() and self._room is None):
                logger.info(
                    "Skipping camera pipeline restart while media transport is not ready: %s%s",
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

    async def _handle_gateway_shutdown(
        self,
        reason: str,
        message: str,
        retry_after_sec: float,
        scope: str,
    ) -> None:
        suppress_livekit_reconnect_noise(retry_after_sec + 30.0)
        if self._uses_external_media_transport():
            return
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

        outage_duration_sec = time.time() - outage_started_at if outage_started_at > 0 else 0.0
        if self._uses_external_media_transport():
            if outage_duration_sec < GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC:
                logger.info(
                    "Control gateway recovered after %s in %.1fs; keeping direct publishers running",
                    reason,
                    outage_duration_sec,
                )
                return

            if self._recovery_restart_task and not self._recovery_restart_task.done():
                self._recovery_restart_task.cancel()

            logger.info(
                "Control gateway recovered after %s in %.1fs; scheduling direct publisher recovery",
                reason,
                outage_duration_sec,
            )
            self._recovery_restart_task = asyncio.create_task(
                self._recover_direct_publish_after_gateway_reconnect(reason)
            )
            return

        if livekit_disconnected:
            logger.info(
                "Control gateway recovered after %s; skipping camera recovery because LiveKit disconnected during the outage",
                reason,
            )
            return

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

    async def _recover_direct_publish_after_gateway_reconnect(self, reason: str) -> None:
        try:
            await asyncio.sleep(3)
            if not self._running or not self._livekit_connected or not self._uses_external_media_transport():
                logger.info(
                    "Skipping delayed direct publisher recovery after %s because direct publishing is no longer ready",
                    reason,
                )
                return

            await self._restart_camera_pipeline(f"gateway recovered after {reason}")
        except asyncio.CancelledError:
            pass

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
        session_id = self._active_room_session_id
        room_disconnected_event = self._active_room_disconnected_event
        if room is None or not self._running or not self._livekit_connected:
            return

        if self._room_reconnect_in_progress:
            return

        try:
            self._room_reconnect_in_progress = True
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

            if room_disconnected_event is not None and self._active_room_session_id == session_id:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(room_disconnected_event.wait(), timeout=6)

            shutdown_task = self._room_shutdown_task
            if shutdown_task and not shutdown_task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await shutdown_task
        finally:
            self._room_reconnect_in_progress = False
