"""Operational helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Literal, Protocol

import aiohttp
from pydantic import ValidationError

from . import __build_id__, __version__
from .auth import AuthFailure, AuthResult, ClientAuthenticator
from .client_contract import ClientComponentBinding
from .client_state import (
    LOCAL_GIT_STATUS_IGNORE_PATHS,
    TELEMETRY_INTERVAL_SEC,
    CameraRuntime,
    logger,
)
from .process_group import run_sandboxed
from .protocol import MAX_WEBSOCKET_MESSAGE_BYTES, RemoteActionsPayload
from .systemd import notify_systemd
from .ws_protocol import WS_EVENTS

try:
    import psutil as _psutil

    _PSUTIL_AVAILABLE = True
except Exception:
    _psutil = None
    _PSUTIL_AVAILABLE = False


class OperationsHost(Protocol):
    config: Any
    stats: Any
    _active_update_action_id: str | None
    _authenticator: ClientAuthenticator
    _camera_runtimes: list[CameraRuntime]
    _capability_manifest: dict[str, object]
    _client_git_branch: str | None
    _client_git_commit: str | None
    _client_git_dirty: bool
    _configured_target_bitrate_kbps: int | None
    _diag_enabled_until: float
    _diagnostics_uploader: Any
    _gateway: Any
    _gateway_task: asyncio.Task[None] | None
    _health_task: asyncio.Task[None] | None
    _heartbeat_task: asyncio.Task[None] | None
    _http_session: aiohttp.ClientSession | None
    _last_cpu_sample: tuple[float, float] | None
    _last_heartbeat_stale_warning_at: float
    _last_telemetry_sent_at: float
    _livekit_connected: bool
    _ota_confirmed: bool
    _planned_disconnect_notice_sent: bool
    _primary_camera_id: str
    _product_milestones: set[str]
    _python_version: str
    _remote_target_bitrate_kbps: int | None
    _repo_root: Path
    _robot_id: str | None
    _room: Any
    _running: bool
    _supervisor_last_tick_monotonic: float
    _systemd_ready_sent: bool
    _tts_task: asyncio.Task[None] | None
    _update_in_progress: bool
    _update_manager: Any

    def _build_health_snapshot(self) -> dict[str, object]: ...

    def _default_target_bitrate_kbps(self, runtime: Any = None) -> int: ...

    def _effective_target_bitrate_kbps(self) -> int: ...

    def _get_session(self) -> aiohttp.ClientSession: ...

    def _get_uptime_sec(self) -> int | None: ...

    def _media_operational(self) -> bool: ...

    def _media_required(self) -> bool: ...

    def _record_fault(
        self,
        code: str,
        subsystem: str,
        *,
        retryable: bool,
        safe_detail: str = "",
    ) -> None: ...

    def _record_product_milestone(self, name: str) -> None: ...

    def _resolve_target_bitrate_kbps(
        self, *, remote: int | None, configured: int | None, default: int
    ) -> int: ...

    def _total_camera_frames(self) -> int: ...

    def _uses_direct_livekit_publisher(self) -> bool: ...

    def _emit_remote_action_result(
        self,
        action_id: str,
        state: Literal["accepted", "completed", "rejected", "failed"],
        code: str,
    ) -> None: ...

    async def _drain_outcome_outbox(self) -> None: ...

    async def _execute_action(self, action: dict[str, Any]) -> None: ...

    async def _heartbeat_loop(self) -> None: ...

    async def _restart_camera_pipeline(self, reason: str, camera_id: str | None = None) -> None: ...

    async def _stop_media_tasks(self) -> None: ...

    async def _sync_on_demand_cameras(self) -> None: ...

    async def _trigger_hardware_stop(self, reason: str) -> bool: ...

    async def _tts_loop(self) -> None: ...


class ClientOpsComponent(ClientComponentBinding[OperationsHost]):
    _ALLOWED_PRODUCT_MILESTONES = frozenset(
        {
            "install_validated",
            "claimed",
            "control_ready",
            "first_media",
            "first_command_ack",
            "reboot_healthy",
            "legacy_config_migrated",
        }
    )

    def _record_product_milestone(self, name: str) -> None:
        if (
            self.host.config.telemetry.product_analytics_enabled
            and name in self._ALLOWED_PRODUCT_MILESTONES
        ):
            self.host._product_milestones.add(name)

    def _raise_if_health_server_failed(self) -> None:
        task = self.host._health_task
        if task is None or not task.done():
            return
        error = task.exception() if not task.cancelled() else None
        if error is not None:
            raise RuntimeError("health server stopped unexpectedly") from error

    def _record_camera_progress(self, runtime: CameraRuntime) -> None:
        frame_count = runtime.manager.frame_count
        if frame_count <= runtime.last_frame_count:
            return

        first_frame = runtime.last_frame_at_monotonic <= 0
        runtime.last_frame_count = frame_count
        runtime.last_frame_at_monotonic = time.monotonic()
        if first_frame and runtime.started_at_monotonic > 0:
            elapsed_ms = (runtime.last_frame_at_monotonic - runtime.started_at_monotonic) * 1000
            self.host.stats.first_frame_ms.append(elapsed_ms)
            if runtime.restart_count > 0:
                self.host.stats.media_restart_ms.append(elapsed_ms)
        runtime.state = "streaming"
        runtime.last_error = None
        if runtime.restart_count > 0 and time.monotonic() - runtime.started_at_monotonic >= 60:
            runtime.restart_count = 0

    async def _restart_finished_camera(self, runtime: CameraRuntime) -> None:
        task = runtime.task
        if task is None or not task.done():
            return

        error = task.exception() if not task.cancelled() else None
        if error:
            runtime.last_error = str(error)[:240]
            self.host._record_fault(
                "camera_task_failed",
                "media",
                retryable=runtime.restart_count < 5,
                safe_detail=type(error).__name__,
            )
            logger.error("Camera task died (%s): %s", runtime.camera_id, error)
        if not self.host._livekit_connected:
            return

        runtime.restart_count += 1
        self.host.stats.camera_task_restarts += 1
        if runtime.restart_count > 5:
            runtime.state = "failed"
            logger.error("Camera %s restarted 5 times - giving up", runtime.camera_id)
            return

        logger.info(
            "Restarting camera pipeline %s (attempt %d/5)",
            runtime.camera_id,
            runtime.restart_count,
        )
        await self.host._restart_camera_pipeline(
            f"supervisor attempt {runtime.restart_count}/5",
            camera_id=runtime.camera_id,
        )

    async def _restart_stalled_camera(self, runtime: CameraRuntime) -> None:
        task = runtime.task
        if task is None or task.done() or not self.host._livekit_connected:
            return
        last_activity = runtime.last_frame_at_monotonic or runtime.started_at_monotonic
        if (
            last_activity <= 0
            or time.monotonic() - last_activity <= 15.0
            or runtime.state in {"restarting", "failed"}
        ):
            return

        runtime.restart_count += 1
        self.host.stats.camera_task_restarts += 1
        if runtime.restart_count > 5:
            runtime.state = "failed"
            runtime.last_error = "media remained stalled after five restarts"
            logger.error("Camera %s remained stalled after 5 restarts", runtime.camera_id)
            return

        runtime.state = "restarting"
        runtime.last_error = "media frame progress stalled"
        self.host._record_fault(
            "camera_frame_stalled",
            "media",
            retryable=True,
            safe_detail=runtime.camera_id,
        )
        logger.warning(
            "Camera %s stalled; restarting (attempt %d/5)",
            runtime.camera_id,
            runtime.restart_count,
        )
        await self.host._restart_camera_pipeline(
            f"frame stall attempt {runtime.restart_count}/5",
            camera_id=runtime.camera_id,
        )

    def _restart_finished_audio(self, runtime: CameraRuntime) -> None:
        audio_task = runtime.manager.audio_task
        if audio_task is None:
            return
        if not (
            self.host._livekit_connected
            and runtime.include_audio
            and audio_task.done()
            and runtime.video_profile.has_audio()
        ):
            return

        error = audio_task.exception() if not audio_task.cancelled() else None
        if error:
            logger.warning("Audio task died - restarting (%s): %s", runtime.camera_id, error)
        if self.host._room is not None:
            runtime.manager.restart_audio(self.host._room, lambda: self.host._running)

    async def _supervise_camera(self, runtime: CameraRuntime) -> None:
        self._record_camera_progress(runtime)
        if runtime.task is not None and runtime.task.done():
            await self._restart_finished_camera(runtime)
        else:
            await self._restart_stalled_camera(runtime)
        self._restart_finished_audio(runtime)

    def _restart_finished_service_tasks(self) -> None:
        if self.host._tts_task and self.host._tts_task.done():
            error = self.host._tts_task.exception() if not self.host._tts_task.cancelled() else None
            if error:
                logger.warning("TTS task died - restarting: %s", error)
            self.host._tts_task = asyncio.create_task(self.host._tts_loop())

        if self.host._heartbeat_task and self.host._heartbeat_task.done():
            logger.warning("Heartbeat task died - restarting")
            self.host._heartbeat_task = asyncio.create_task(self.host._heartbeat_loop())

        if self.host._gateway_task and self.host._gateway_task.done():
            error = (
                self.host._gateway_task.exception()
                if not self.host._gateway_task.cancelled()
                else None
            )
            if error:
                logger.warning("Gateway task died - restarting: %s", error)
            self.host._gateway_task = asyncio.create_task(self.host._gateway.run())

    async def _confirm_ready_update(self) -> None:
        ready_to_confirm = (
            self.host._update_manager is not None
            and not self.host._ota_confirmed
            and not self.host._update_in_progress
            and self.host._gateway.connected
            and (
                not self.host._media_required()
                or (self.host._media_operational() and self.host._total_camera_frames() > 0)
            )
        )
        if not ready_to_confirm:
            return

        completed_action_id = await asyncio.to_thread(self.host._update_manager.confirm)
        self.host._ota_confirmed = True
        self.host._record_product_milestone("reboot_healthy")
        logger.info("OTA release confirmed ready")
        if completed_action_id is not None:
            self.host._emit_remote_action_result(completed_action_id, "completed", "installed")

    def _publish_systemd_status(self) -> None:
        health = self.host._build_health_snapshot()
        if health["ready"] and not self.host._systemd_ready_sent:
            notify_systemd("READY=1\nSTATUS=Ready")
            self.host._systemd_ready_sent = True
            return
        if not self.host._systemd_ready_sent:
            return

        status = "Ready" if health["ready"] else "Degraded"
        degraded_values = health["degraded"]
        degraded = (
            ",".join(str(value) for value in degraded_values)
            if isinstance(degraded_values, list)
            else ""
        )
        notify_systemd(f"STATUS={status}{': ' + degraded if degraded else ''}")

    def _warn_if_heartbeat_is_stale(self) -> None:
        age = time.time() - self.host.stats.last_heartbeat_at
        if age <= 60:
            return
        now = time.time()
        if now - self.host._last_heartbeat_stale_warning_at < 30:
            return
        logger.warning("API heartbeat stale: last success %.0fs ago", age)
        self.host._last_heartbeat_stale_warning_at = now

    async def _supervisor(self) -> None:
        logger.info("Supervisor started")
        while self.host._running:
            await asyncio.sleep(1)
            self.host._supervisor_last_tick_monotonic = time.monotonic()
            notify_systemd("WATCHDOG=1")
            self._raise_if_health_server_failed()

            for runtime in self.host._camera_runtimes:
                await self._supervise_camera(runtime)
            self._restart_finished_service_tasks()

            if self.host._gateway.connected:
                self.host._record_product_milestone("control_ready")
                await self.host._drain_outcome_outbox()
            if self.host._total_camera_frames() > 0:
                self.host._record_product_milestone("first_media")

            await self._confirm_ready_update()
            self._publish_systemd_status()
            self._warn_if_heartbeat_is_stale()

        logger.info("Supervisor stopped")

    async def _heartbeat_loop(self) -> None:
        while self.host._running:
            try:
                sent = await self.host._gateway.send_event(
                    WS_EVENTS["ROBOT_HEARTBEAT"], {"robotId": self.host._robot_id}
                )
                if sent:
                    self.host.stats.last_heartbeat_at = time.time()
                else:
                    session = self.host._get_session()
                    headers = {"Content-Type": "application/json"}
                    robot_auth_token = (
                        self.host.config.server.robot_auth_token_value() or ""
                    ).strip()
                    if robot_auth_token:
                        headers["Authorization"] = f"Bearer {robot_auth_token}"
                    else:
                        await asyncio.sleep(1)
                        continue
                    async with session.post(
                        f"{self.host.config.server.api_url}/api/v1/robots/heartbeat",
                        json={},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5),
                        allow_redirects=False,
                    ) as resp:
                        if resp.status in (200, 201):
                            self.host.stats.last_heartbeat_at = time.time()

                if time.time() - self.host._last_telemetry_sent_at >= TELEMETRY_INTERVAL_SEC:
                    await self._send_telemetry()
                    self.host._last_telemetry_sent_at = time.time()
            except Exception as e:
                logger.debug("Heartbeat error (non-fatal): %s", e)
            await asyncio.sleep(15)

    async def _send_telemetry(self) -> None:
        if not self.host.config.telemetry.operational_enabled:
            return
        streamer_version: str | None = None
        for runtime in self.host._camera_runtimes:
            version = runtime.video_profile.botparty_streamer_version()
            if version:
                streamer_version = version
                break
        if not streamer_version:
            version_file = self.host._repo_root / ".botparty" / "bin" / "botparty-streamer.version"
            try:
                raw = version_file.read_text(encoding="utf-8").strip()
                streamer_version = raw or None
            except Exception:
                streamer_version = None

        payload: dict[str, Any] = {
            "clientVersion": __version__,
            "buildId": __build_id__,
            "gitBranch": self.host._client_git_branch,
            "gitCommit": self.host._client_git_commit,
            "gitDirty": self.host._client_git_dirty,
            "pythonVersion": self.host._python_version,
            "cpuPercent": self._read_cpu_percent(),
            "memoryPercent": self._read_memory_percent(),
            "temperatureC": self._read_temperature_c(),
            "uptimeSec": self.host._get_uptime_sec(),
            "controlConnected": self.host._gateway.connected,
            "livekitConnected": self.host._livekit_connected,
            "commandsReceived": self.host.stats.commands_received,
            "cameraFrames": self.host._total_camera_frames(),
            "controlReconnects": self.host.stats.reconnect_attempts,
            "mediaReconnects": self.host.stats.media_reconnects,
            "controlDisconnects": self.host.stats.control_disconnects,
            "safetyStops": self.host.stats.safety_stops,
            "watchdogStops": self.host.stats.watchdog_stops,
            "emergencyStops": self.host.stats.emergency_stops,
            "commandQueueDrops": self.host.stats.command_queue_drops,
            "staleCommands": self.host.stats.stale_commands,
        }
        if streamer_version:
            payload["botpartyStreamerVersion"] = streamer_version
        if self.host.config.telemetry.product_analytics_enabled:
            payload["productMilestones"] = sorted(self.host._product_milestones)
        if _PSUTIL_AVAILABLE:
            try:
                payload["cpuPercent"] = float(_psutil.cpu_percent(interval=None))
                payload["memoryPercent"] = float(_psutil.virtual_memory().percent)
                boot_time = float(_psutil.boot_time())
                payload["uptimeSec"] = max(0, int(time.time() - boot_time))
            except Exception:
                pass

        sent = await self.host._gateway.send_event(WS_EVENTS["ROBOT_TELEMETRY"], payload)
        if not sent:
            session = self.host._get_session()
            headers = {"Content-Type": "application/json"}
            robot_auth_token = (self.host.config.server.robot_auth_token_value() or "").strip()
            if robot_auth_token:
                headers["Authorization"] = f"Bearer {robot_auth_token}"
            if not robot_auth_token:
                return
            async with session.post(
                f"{self.host.config.server.api_url}/api/v1/robots/telemetry",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=False,
            ) as response:
                await response.content.read(4_097)

    def _read_temperature_c(self) -> float | None:
        for path in (
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input",
        ):
            try:
                with open(path, encoding="utf-8") as fh:
                    value = float(fh.read().strip())
                if value > 1000:
                    value /= 1000.0
                if -40 <= value <= 150:
                    return value
            except Exception:
                continue
        return None

    def _get_uptime_sec(self) -> int | None:
        try:
            with open("/proc/uptime", encoding="utf-8") as fh:
                return max(0, int(float(fh.read().split()[0])))
        except Exception:
            return None

    def _prime_cpu_sample(self) -> None:
        try:
            with open("/proc/stat", encoding="utf-8") as fh:
                parts = fh.readline().split()
            if len(parts) < 5 or parts[0] != "cpu":
                return
            values = [float(v) for v in parts[1:]]
            self.host._last_cpu_sample = (values[3], sum(values))
        except Exception:
            pass

    def _read_cpu_percent(self) -> float | None:
        try:
            with open("/proc/stat", encoding="utf-8") as fh:
                parts = fh.readline().split()
            if len(parts) < 5 or parts[0] != "cpu":
                return None

            values = [float(value) for value in parts[1:]]
            idle = values[3]
            total = sum(values)
            previous = self.host._last_cpu_sample
            self.host._last_cpu_sample = (idle, total)

            if previous is None:
                return None

            prev_idle, prev_total = previous
            total_delta = total - prev_total
            idle_delta = idle - prev_idle
            if total_delta <= 0:
                return None

            usage = 100.0 * (1.0 - (idle_delta / total_delta))
            return max(0.0, min(100.0, usage))
        except Exception:
            return None

    def _read_memory_percent(self) -> float | None:
        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo", encoding="utf-8") as handle:
                for line in handle:
                    key, value = line.split(":", 1)
                    meminfo[key] = int(value.strip().split()[0])

            total = meminfo.get("MemTotal")
            available = meminfo.get("MemAvailable")
            if not total or available is None or total <= 0:
                return None

            used = total - available
            usage = (used / total) * 100.0
            return max(0.0, min(100.0, usage))
        except Exception:
            return None

    async def _actions_loop(self) -> None:
        while self.host._running:
            try:
                if self.host._gateway.connected:
                    await asyncio.sleep(3)
                    continue

                session = self.host._get_session()
                headers = {"Content-Type": "application/json"}
                robot_auth_token = (self.host.config.server.robot_auth_token_value() or "").strip()
                if not robot_auth_token:
                    await asyncio.sleep(3)
                    continue
                headers["Authorization"] = f"Bearer {robot_auth_token}"
                async with session.post(
                    f"{self.host.config.server.api_url}/api/v1/robots/actions/poll",
                    json={},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                    allow_redirects=False,
                ) as resp:
                    if resp.status in (200, 201):
                        raw = await resp.content.read(MAX_WEBSOCKET_MESSAGE_BYTES + 1)
                        if len(raw) > MAX_WEBSOCKET_MESSAGE_BYTES:
                            logger.warning("Rejected oversized action poll response")
                            await asyncio.sleep(3)
                            continue
                        data = json.loads(raw)
                        if isinstance(data, dict):
                            await self._apply_remote_actions_payload(data)
            except Exception as e:
                logger.debug("Action poll error (non-fatal): %s", e)
            await asyncio.sleep(3)

    async def _apply_remote_actions_payload(self, payload: dict[str, Any]) -> None:
        try:
            remote_payload = RemoteActionsPayload.model_validate(payload)
        except ValidationError as exc:
            logger.warning("Rejected invalid remote action payload: %s", exc)
            return
        policy = remote_payload.stream
        if policy is not None:
            next_remote_bitrate = policy.target_bitrate_kbps
            previous_primary = self.host._primary_camera_id
            if policy.active_camera_id is not None:
                active_camera = policy.active_camera_id.strip().lower()
                enabled_camera_ids = {runtime.camera_id for runtime in self.host._camera_runtimes}
                if active_camera not in enabled_camera_ids:
                    logger.warning(
                        "Rejected unknown active camera id in stream policy: %s",
                        active_camera,
                    )
                else:
                    self.host._primary_camera_id = active_camera
                    await self.host._sync_on_demand_cameras()

            next_effective_bitrate = self.host._resolve_target_bitrate_kbps(
                remote=next_remote_bitrate,
                configured=self.host._configured_target_bitrate_kbps,
                default=self.host._default_target_bitrate_kbps(),
            )
            if (
                next_effective_bitrate != self.host._effective_target_bitrate_kbps()
                or next_remote_bitrate != self.host._remote_target_bitrate_kbps
                or previous_primary != self.host._primary_camera_id
            ):
                self.host._remote_target_bitrate_kbps = next_remote_bitrate
                logger.info(
                    "Remote stream policy: remoteTargetBitrateKbps=%s "
                    "effectiveTargetBitrateKbps=%d",
                    self.host._remote_target_bitrate_kbps,
                    self.host._effective_target_bitrate_kbps(),
                )
                if self.host._livekit_connected:
                    await self.host._restart_camera_pipeline("stream policy updated")

        for action in remote_payload.actions:
            await self.host._execute_action(action.model_dump(by_alias=True, exclude_none=True))

    async def _diagnostics_upload_loop(self) -> None:
        while self.host._running:
            try:
                await self.host._diagnostics_uploader.upload_once(self.host._diag_enabled_until)
            except Exception as e:
                logger.debug("Diagnostics upload error (non-fatal): %s", e)
            await asyncio.sleep(2)

    async def _authenticate(self) -> AuthResult | None:
        authenticator = getattr(self.host, "_authenticator", None)
        if authenticator is None:
            authenticator = ClientAuthenticator(self.host.config, self.host._get_session)
            self.host._authenticator = authenticator
        publish_camera_ids = (
            [runtime.camera_id for runtime in self.host._camera_runtimes]
            if self.host._uses_direct_livekit_publisher()
            else []
        )
        started = time.monotonic()
        outcome = await authenticator.claim(
            publish_camera_ids=publish_camera_ids,
            capabilities=self.host._capability_manifest,
        )
        try:
            stats = self.host.stats
        except AttributeError:
            stats = None
        claim_samples = getattr(stats, "claim_latency_ms", None)
        if claim_samples is not None:
            claim_samples.append((time.monotonic() - started) * 1000)
        if isinstance(outcome, AuthFailure):
            logger.error("Authentication failed (%s): %s", outcome.code, outcome.detail)
            return None
        self.host._remote_target_bitrate_kbps = outcome.target_bitrate_kbps
        logger.info(
            "Video target bitrate: remote=%s configured=%s effective=%d kbps",
            self.host._remote_target_bitrate_kbps,
            self.host._configured_target_bitrate_kbps,
            self.host._effective_target_bitrate_kbps(),
        )
        return outcome

    def _read_git_metadata(self) -> tuple[str | None, str | None, bool]:
        if not (self.host._repo_root / ".git").exists():
            return None, None, False

        def read_git_output(args: list[str]) -> str | None:
            try:
                result = run_sandboxed(
                    args,
                    cwd=self.host._repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                )
            except (OSError, subprocess.SubprocessError):
                return None

            if result.returncode != 0:
                return None
            value = result.stdout.strip()
            return value or None

        branch = read_git_output(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        commit = read_git_output(["git", "rev-parse", "--short", "HEAD"])
        dirty_output = read_git_output(["git", "status", "--porcelain", "--untracked-files=all"])
        if not dirty_output:
            return branch, commit, False

        relevant_changes = []
        for line in dirty_output.splitlines():
            candidate = line[3:] if len(line) > 3 else line
            normalized = candidate.strip()
            if " -> " in normalized:
                normalized = normalized.split(" -> ", 1)[1].strip()
            if any(
                normalized == ignored or normalized.startswith(ignored)
                for ignored in LOCAL_GIT_STATUS_IGNORE_PATHS
            ):
                continue
            relevant_changes.append(normalized)

        return branch, commit, bool(relevant_changes)

    async def _perform_client_update(self, action_id: str | None = None) -> None:
        if self.host._update_manager is None:
            logger.warning("Skipping update_client: signed transactional OTA is not enabled")
            if action_id is not None:
                self.host._emit_remote_action_result(action_id, "failed", "ota_disabled")
            return

        if not self.host._update_in_progress:
            self.host._update_in_progress = True
            self.host._active_update_action_id = action_id
        elif self.host._active_update_action_id != action_id:
            if action_id is not None:
                self.host._emit_remote_action_result(action_id, "rejected", "update_in_progress")
            return
        try:
            if not await self.host._trigger_hardware_stop("ota_update"):
                raise RuntimeError("OTA refused because hardware stop was not confirmed")
            config_path = self.host.config._source_path
            if config_path is None:
                raise RuntimeError("OTA requires the canonical loaded configuration path")
            executable = await asyncio.to_thread(
                self.host._update_manager.install,
                config_path,
                action_id,
            )
            logger.info("Verified OTA release installed; activating new slot")
            await self._restart_process_after_update(executable)
        except Exception as exc:
            logger.error("Client update failed: %s", exc)
            if action_id is not None:
                self.host._emit_remote_action_result(action_id, "failed", "update_failed")
        finally:
            self.host._update_in_progress = False
            self.host._active_update_action_id = None

    async def _restart_process_after_update(self, executable: Path | None = None) -> None:
        logger.info("Restarting client process after successful update")
        self.host._planned_disconnect_notice_sent = True
        self.host._livekit_connected = False

        await self.host._stop_media_tasks()

        room = self.host._room
        if room is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(room.disconnect(), timeout=5)

        if self.host._http_session and not self.host._http_session.closed:
            with contextlib.suppress(Exception):
                await self.host._http_session.close()

        await self.host._trigger_hardware_stop("process_restart")

        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.host._gateway.close(), timeout=2)

        # Brief pause to let the event loop flush any final callbacks
        await asyncio.sleep(0.1)

        target = str(executable or Path(sys.executable))
        try:
            os.execv(target, [target, "-m", "botparty_robot", *sys.argv[1:]])
        except OSError as exc:
            if self.host._update_manager is not None:
                await asyncio.to_thread(self.host._update_manager.rollback_current_update)
            self.host._running = False
            raise RuntimeError("OTA process activation failed and was rolled back") from exc


ClientOpsMixin = ClientOpsComponent
