"""Media and reconnect helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Protocol

from .camera import CameraManager
from .client_contract import ClientComponentBinding
from .client_state import (
    GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC,
    CameraRuntime,
    MediaManager,
    logger,
    suppress_livekit_reconnect_noise,
)
from .config import normalize_cameras
from .publisher import LiveKitPublisherManager
from .video import create_video_profile


class MediaHost(Protocol):
    config: Any
    stats: Any
    _active_room_disconnected_event: asyncio.Event | None
    _active_room_session_id: int
    _camera_restart_lock: asyncio.Lock
    _camera_runtimes: list[CameraRuntime]
    _configured_target_bitrate_kbps: int | None
    _gateway_outage_scope: str | None
    _gateway_outage_started_at: float
    _livekit_connected: bool
    _livekit_disconnected_during_gateway_outage: bool
    _livekit_publish_token: str | None
    _livekit_publish_tokens: dict[str, str]
    _livekit_reconnect_task: asyncio.Task[None] | None
    _planned_disconnect_notice_sent: bool
    _planned_reconnect_at: float
    _planned_reconnect_reason: str | None
    _primary_camera_id: str
    _recovery_restart_task: asyncio.Task[None] | None
    _remote_target_bitrate_kbps: int | None
    _room: Any
    _room_reconnect_in_progress: bool
    _room_shutdown_task: asyncio.Task[None] | None
    _running: bool
    _shutdown_disconnect_task: asyncio.Task[None] | None

    def _default_target_bitrate_kbps(self, runtime: CameraRuntime | None = None) -> int: ...

    def _resolve_target_bitrate_kbps(
        self, *, remote: int | None, configured: int | None, default: int
    ) -> int: ...

    def _uses_direct_livekit_publisher(self) -> bool: ...

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None: ...

    async def _stop_media_tasks(self) -> None: ...

    async def _trigger_hardware_stop(self, reason: str) -> bool: ...


class ClientMediaComponent(ClientComponentBinding[MediaHost]):
    def _build_initial_camera_runtime_state(
        self,
    ) -> tuple[list[CameraRuntime], str]:
        camera_runtimes = self._build_camera_runtimes()
        if camera_runtimes:
            primary_runtime = next(
                (
                    runtime
                    for runtime in camera_runtimes
                    if runtime.role.strip().lower() == "primary"
                ),
                camera_runtimes[0],
            )
            return camera_runtimes, primary_runtime.camera_id

        return [], "front"

    def _uses_direct_livekit_publisher(self) -> bool:
        return bool(self.host._camera_runtimes) and all(
            runtime.video_profile.publish_transport() == "livekit_direct"
            for runtime in self.host._camera_runtimes
        )

    def _uses_external_media_transport(self) -> bool:
        return self.host._uses_direct_livekit_publisher()

    def _validate_media_mode(self) -> None:
        transports = {
            runtime.video_profile.publish_transport() for runtime in self.host._camera_runtimes
        }
        if len(transports) > 1:
            raise ValueError(
                "External and legacy LiveKit camera profiles cannot be mixed in one client config"
            )

    def _build_camera_runtimes(self) -> list[CameraRuntime]:
        normalized = normalize_cameras(self.host.config)
        runtimes: list[CameraRuntime] = []
        requested_audio_source = self.host.config.audio_source_camera_id
        audio_source_selected = False

        for entry in normalized:
            if not entry.enabled:
                continue

            derived_config = self.host.config.model_copy(
                deep=True,
                update={
                    "camera": entry.camera,
                    "video": entry.video,
                },
            )
            video_profile = create_video_profile(derived_config)
            include_audio = video_profile.has_audio() and (
                entry.id == requested_audio_source
                if requested_audio_source is not None
                else not audio_source_selected
            )
            if include_audio:
                audio_source_selected = True
            track_name = "camera" if len(normalized) == 1 else f"camera.{entry.id}"
            manager: MediaManager
            if video_profile.publish_transport() == "livekit_direct":
                camera_id = entry.id

                def camera_token(selected_camera_id: str = camera_id) -> str | None:
                    return (
                        self.host._livekit_publish_tokens.get(selected_camera_id)
                        or self.host._livekit_publish_token
                    )

                manager = LiveKitPublisherManager(
                    derived_config,
                    video_profile,
                    token_fn=camera_token,
                    audio_token_fn=lambda: self.host._livekit_publish_token,
                    livekit_url_fn=lambda: self.host.config.server.livekit_url,
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

        if requested_audio_source is not None and not audio_source_selected:
            raise ValueError(
                f"Camera {requested_audio_source!r} cannot be the audio source because its "
                "video profile has no audio input"
            )

        return runtimes

    def _resolve_primary_camera_id(self) -> str:
        for runtime in self.host._camera_runtimes:
            if runtime.role.strip().lower() == "primary":
                return runtime.camera_id
        if self.host._camera_runtimes:
            return self.host._camera_runtimes[0].camera_id
        return "front"

    def _get_primary_runtime(self) -> CameraRuntime | None:
        if not self.host._camera_runtimes:
            return None
        for runtime in self.host._camera_runtimes:
            if runtime.camera_id == self.host._primary_camera_id:
                return runtime
        return self.host._camera_runtimes[0]

    def _total_camera_frames(self) -> int:
        return sum(runtime.manager.frame_count for runtime in self.host._camera_runtimes)

    def _start_camera(self, runtime: CameraRuntime) -> Any:
        return runtime.manager.run(
            self.host._room,
            self._target_bitrate_for_runtime(runtime),
            lambda: self.host._running,
            lambda: self.host._livekit_connected,
        )

    def _parse_target_bitrate_kbps(self, value: Any) -> int | None:
        if isinstance(value, (int, float)) and 150 <= value <= 3000:
            return int(value)
        return None

    def _default_target_bitrate_kbps(self, runtime: CameraRuntime | None = None) -> int:
        active_config = runtime.config if runtime is not None else self.host.config
        pixels_per_second = (
            active_config.camera.width
            * active_config.camera.height
            * max(active_config.camera.fps, 1)
        )
        if pixels_per_second <= 7_500_000:
            return 800
        if pixels_per_second <= 28_000_000:
            return 1500
        return 2200

    def _resolve_target_bitrate_kbps(
        self,
        *,
        remote: int | None,
        configured: int | None,
        default: int,
    ) -> int:
        if remote is not None and configured is not None:
            return min(remote, configured)
        return remote or configured or default

    def _effective_target_bitrate_kbps(self) -> int:
        return self.host._resolve_target_bitrate_kbps(
            remote=self.host._remote_target_bitrate_kbps,
            configured=self.host._configured_target_bitrate_kbps,
            default=self.host._default_target_bitrate_kbps(),
        )

    def _target_bitrate_for_runtime(self, runtime: CameraRuntime) -> int | None:
        configured = self._parse_target_bitrate_kbps(
            runtime.config.video.options.get("target_bitrate_kbps")
        )
        return self.host._resolve_target_bitrate_kbps(
            remote=self.host._remote_target_bitrate_kbps,
            configured=configured,
            default=self.host._default_target_bitrate_kbps(runtime),
        )

    async def _start_all_cameras(self) -> None:
        for runtime in self.host._camera_runtimes:
            runtime.restart_count = 0
            runtime.started_at_monotonic = time.monotonic()
            runtime.last_frame_at_monotonic = 0.0
            runtime.last_frame_count = runtime.manager.frame_count
            should_publish = (
                runtime.publish_mode == "always_on"
                or runtime.camera_id == self.host._primary_camera_id
            )
            runtime.state = "starting" if should_publish else "idle"
            runtime.last_error = None
            runtime.task = (
                asyncio.create_task(self._start_camera(runtime)) if should_publish else None
            )

    async def _sync_on_demand_cameras(self) -> None:
        for runtime in self.host._camera_runtimes:
            if runtime.publish_mode != "on_demand":
                continue
            should_publish = runtime.camera_id == self.host._primary_camera_id
            active = runtime.task is not None and not runtime.task.done()
            if should_publish and not active:
                runtime.started_at_monotonic = time.monotonic()
                runtime.last_frame_at_monotonic = 0.0
                runtime.last_frame_count = runtime.manager.frame_count
                runtime.state = "warming_up"
                runtime.last_error = None
                runtime.task = asyncio.create_task(self._start_camera(runtime))
            elif not should_publish and active:
                await self._cancel_camera_task(runtime)
                runtime.state = "idle"

    async def _cancel_camera_task(self, runtime: CameraRuntime, timeout_sec: float = 6.5) -> None:
        task = runtime.task
        if not task or task.done():
            return

        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=timeout_sec)
        if pending:
            runtime.state = "failed"
            runtime.last_error = "publisher did not stop before its deadline"
            logger.warning(
                "Camera task did not shut down within %.1fs; waiting for device release",
                timeout_sec,
            )
            return

        with contextlib.suppress(asyncio.CancelledError, Exception):
            await next(iter(done))

        runtime.task = None
        runtime.state = "stopped"

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None:
        async with self.host._camera_restart_lock:
            if not self.host._livekit_connected or (
                not self._uses_external_media_transport() and self.host._room is None
            ):
                logger.info(
                    "Skipping camera pipeline restart while media transport is not ready: %s%s",
                    reason,
                    f" ({camera_id})" if camera_id else "",
                )
                return

            logger.info(
                "Restarting camera pipeline: %s%s", reason, f" ({camera_id})" if camera_id else ""
            )
            targets = (
                [
                    runtime
                    for runtime in self.host._camera_runtimes
                    if runtime.camera_id == camera_id
                ]
                if camera_id
                else [
                    runtime
                    for runtime in self.host._camera_runtimes
                    if runtime.publish_mode == "always_on"
                    or runtime.camera_id == self.host._primary_camera_id
                ]
            )

            for runtime in targets:
                await self._cancel_camera_task(runtime)
                if runtime.task is not None and not runtime.task.done():
                    logger.error(
                        "Camera %s still owns its publisher; restart is blocked",
                        runtime.camera_id,
                    )
                    continue
                runtime.video_profile = create_video_profile(runtime.config)
                runtime.manager.video_profile = runtime.video_profile
                runtime.started_at_monotonic = time.monotonic()
                runtime.last_frame_at_monotonic = 0.0
                runtime.last_frame_count = int(getattr(runtime.manager, "frame_count", 0))
                runtime.state = "starting"
                runtime.task = asyncio.create_task(self._start_camera(runtime))

    async def _stop_media_tasks(self) -> None:
        for runtime in self.host._camera_runtimes:
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
        self.host._planned_reconnect_at = max(self.host._planned_reconnect_at, reconnect_at)
        self.host._planned_reconnect_reason = reason
        self.host._gateway_outage_scope = scope
        self.host._livekit_disconnected_during_gateway_outage = False

        if scope != "full" or not self.host._livekit_connected or self.host._room is None:
            return

        if self.host._planned_disconnect_notice_sent:
            return

        self.host._planned_disconnect_notice_sent = True
        logger.info(
            "Planned %s across full stack; disconnecting LiveKit early to avoid noisy retries",
            reason,
        )

        if self.host._shutdown_disconnect_task and not self.host._shutdown_disconnect_task.done():
            return

        self.host._shutdown_disconnect_task = asyncio.create_task(
            self._disconnect_livekit_for_shutdown(message)
        )

    async def _disconnect_livekit_for_shutdown(self, message: str) -> None:
        room = self.host._room
        if room is None:
            return

        logger.info("%s", message)
        self.host._livekit_connected = False
        try:
            await room.disconnect()
        except Exception as exc:
            logger.debug("LiveKit disconnect during planned shutdown failed: %s", exc)

    async def _handle_gateway_disconnected(self, scope: str) -> None:
        await self.host._trigger_hardware_stop("control_disconnected")
        self.host.stats.control_disconnects += 1
        self.host.stats.last_control_disconnect_reason = scope
        if scope != "app":
            return
        if self.host._gateway_outage_started_at <= 0:
            self.host._gateway_outage_started_at = time.time()
        self.host._gateway_outage_scope = scope

    async def _handle_gateway_reconnected(self, reason: str, scope: str) -> None:
        outage_started_at = self.host._gateway_outage_started_at
        outage_scope = self.host._gateway_outage_scope
        livekit_disconnected = self.host._livekit_disconnected_during_gateway_outage
        self.host._gateway_outage_started_at = 0.0
        self.host._gateway_outage_scope = None
        self.host._livekit_disconnected_during_gateway_outage = False

        if outage_started_at > 0:
            self.host.stats.control_reconnect_ms.append((time.time() - outage_started_at) * 1000)

        if scope != "app" or outage_scope != "app":
            return

        if not self.host._livekit_connected:
            return

        outage_duration_sec = time.time() - outage_started_at if outage_started_at > 0 else 0.0
        if self._uses_external_media_transport():
            if outage_duration_sec < GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC:
                logger.info(
                    "Control gateway recovered after %s in %.1fs; "
                    "keeping direct publishers running",
                    reason,
                    outage_duration_sec,
                )
                return

            if self.host._recovery_restart_task and not self.host._recovery_restart_task.done():
                self.host._recovery_restart_task.cancel()

            logger.info(
                "Control gateway recovered after %s in %.1fs; scheduling direct publisher recovery",
                reason,
                outage_duration_sec,
            )
            self.host._recovery_restart_task = asyncio.create_task(
                self._recover_direct_publish_after_gateway_reconnect(reason)
            )
            return

        if livekit_disconnected:
            logger.info(
                "Control gateway recovered after %s; skipping camera recovery because "
                "LiveKit disconnected during the outage",
                reason,
            )
            return

        if outage_duration_sec < GATEWAY_RECOVERY_RESTART_THRESHOLD_SEC:
            logger.info(
                "Control gateway recovered after %s in %.1fs; keeping existing camera publish "
                "because the stream likely survived",
                reason,
                outage_duration_sec,
            )
            return

        if self.host._recovery_restart_task and not self.host._recovery_restart_task.done():
            self.host._recovery_restart_task.cancel()

        logger.info(
            "Control gateway recovered after %s in %.1fs; scheduling LiveKit room recovery",
            reason,
            outage_duration_sec,
        )
        self.host._recovery_restart_task = asyncio.create_task(
            self._recover_livekit_room_after_gateway_reconnect(reason)
        )

    async def _recover_direct_publish_after_gateway_reconnect(self, reason: str) -> None:
        try:
            await asyncio.sleep(3)
            if (
                not self.host._running
                or not self.host._livekit_connected
                or not self._uses_external_media_transport()
            ):
                logger.info(
                    "Skipping delayed direct publisher recovery after %s because direct "
                    "publishing is no longer ready",
                    reason,
                )
                return

            await self.host._restart_camera_pipeline(f"gateway recovered after {reason}")
        except asyncio.CancelledError:
            pass

    async def _recover_livekit_room_after_gateway_reconnect(self, reason: str) -> None:
        try:
            await asyncio.sleep(5)
            if (
                not self.host._running
                or not self.host._livekit_connected
                or self.host._room is None
            ):
                logger.info(
                    "Skipping delayed LiveKit recovery after %s because the room is no longer "
                    "ready",
                    reason,
                )
                return

            if self.host._livekit_reconnect_task and not self.host._livekit_reconnect_task.done():
                return

            self.host._livekit_reconnect_task = asyncio.create_task(
                self._force_livekit_reconnect_after_gateway_recovery(reason)
            )
        except asyncio.CancelledError:
            pass

    async def _force_livekit_reconnect_after_gateway_recovery(self, reason: str) -> None:
        room = self.host._room
        session_id = self.host._active_room_session_id
        room_disconnected_event = self.host._active_room_disconnected_event
        if room is None or not self.host._running or not self.host._livekit_connected:
            return

        if self.host._room_reconnect_in_progress:
            return

        try:
            self.host._room_reconnect_in_progress = True
            logger.info(
                "Forcing LiveKit room reconnect after %s so streams recover cleanly",
                reason,
            )

            self.host._planned_reconnect_reason = reason
            self.host._planned_reconnect_at = time.time() + 5
            self.host._livekit_connected = False

            await self.host._stop_media_tasks()

            try:
                await asyncio.wait_for(room.disconnect(), timeout=5)
            except asyncio.TimeoutError:
                logger.warning("Timed out while disconnecting LiveKit room during recovery")
            except Exception as exc:
                logger.debug("LiveKit disconnect during recovery failed: %s", exc)

            if (
                room_disconnected_event is not None
                and self.host._active_room_session_id == session_id
            ):
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(room_disconnected_event.wait(), timeout=6)

            shutdown_task = self.host._room_shutdown_task
            if shutdown_task and not shutdown_task.done():
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await shutdown_task
        finally:
            self.host._room_reconnect_in_progress = False


ClientMediaMixin = ClientMediaComponent
