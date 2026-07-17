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
from typing import Any

import aiohttp
from pydantic import ValidationError

from . import __build_id__, __version__
from .auth import AuthFailure, AuthResult, ClientAuthenticator
from .client_state import LOCAL_GIT_STATUS_IGNORE_PATHS, TELEMETRY_INTERVAL_SEC, logger
from .protocol import MAX_WEBSOCKET_MESSAGE_BYTES, RemoteAction
from .systemd import notify_systemd
from .ws_protocol import WS_EVENTS

try:
    import psutil as _psutil  # type: ignore

    _PSUTIL_AVAILABLE = True
except Exception:
    _psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False


class ClientOpsMixin:
    _ALLOWED_PRODUCT_MILESTONES = frozenset(
        {
            "install_validated",
            "claimed",
            "control_ready",
            "first_media",
            "first_command_ack",
            "reboot_healthy",
        }
    )

    def _record_product_milestone(self, name: str) -> None:
        if (
            self.config.telemetry.product_analytics_enabled
            and name in self._ALLOWED_PRODUCT_MILESTONES
        ):
            self._product_milestones.add(name)

    async def _supervisor(self) -> None:
        logger.info("Supervisor started")
        while self._running:
            await asyncio.sleep(1)
            notify_systemd("WATCHDOG=1")

            for runtime in self._camera_runtimes:
                task = runtime.task
                frame_count = runtime.manager.frame_count
                if frame_count > runtime.last_frame_count:
                    runtime.last_frame_count = frame_count
                    runtime.last_frame_at_monotonic = time.monotonic()
                    runtime.state = "streaming"
                    runtime.last_error = None
                    if (
                        runtime.restart_count > 0
                        and time.monotonic() - runtime.started_at_monotonic >= 60
                    ):
                        runtime.restart_count = 0
                if task and task.done():
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
                        runtime.last_error = str(exc)[:240]
                        logger.error("Camera task died (%s): %s", runtime.camera_id, exc)
                    if self._livekit_connected:
                        runtime.restart_count += 1
                        self.stats.camera_task_restarts += 1
                        if runtime.restart_count <= 5:
                            logger.info(
                                "Restarting camera pipeline %s (attempt %d/5)",
                                runtime.camera_id,
                                runtime.restart_count,
                            )
                            await self._restart_camera_pipeline(
                                f"supervisor attempt {runtime.restart_count}/5",
                                camera_id=runtime.camera_id,
                            )
                        else:
                            runtime.state = "failed"
                            logger.error(
                                "Camera %s restarted 5 times - giving up", runtime.camera_id
                            )

                audio = runtime.manager.audio_task
                if (
                    self._livekit_connected
                    and runtime.include_audio
                    and audio
                    and audio.done()
                    and runtime.video_profile.has_audio()
                ):
                    exc = audio.exception() if not audio.cancelled() else None
                    if exc:
                        logger.warning(
                            "Audio task died - restarting (%s): %s", runtime.camera_id, exc
                        )
                    runtime.manager.restart_audio(self._room, lambda: self._running)

            if self._tts_task and self._tts_task.done():
                exc = self._tts_task.exception() if not self._tts_task.cancelled() else None
                if exc:
                    logger.warning("TTS task died - restarting: %s", exc)
                self._tts_task = asyncio.create_task(self._tts_loop())

            if self._heartbeat_task and self._heartbeat_task.done():
                logger.warning("Heartbeat task died - restarting")
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            if self._gateway_task and self._gateway_task.done():
                exc = self._gateway_task.exception() if not self._gateway_task.cancelled() else None
                if exc:
                    logger.warning("Gateway task died - restarting: %s", exc)
                self._gateway_task = asyncio.create_task(self._gateway.run())

            if self._gateway.connected:
                self._record_product_milestone("control_ready")
            if self._total_camera_frames() > 0:
                self._record_product_milestone("first_media")

            if (
                self._update_manager is not None
                and not self._ota_confirmed
                and self._gateway.connected
                and (
                    not self._media_required()
                    or (self._media_operational() and self._total_camera_frames() > 0)
                )
            ):
                await asyncio.to_thread(self._update_manager.confirm)
                self._ota_confirmed = True
                self._record_product_milestone("reboot_healthy")
                logger.info("OTA release confirmed ready")

            age = time.time() - self.stats.last_heartbeat_at
            if age > 60:
                now = time.time()
                if now - self._last_heartbeat_stale_warning_at >= 30:
                    logger.warning("API heartbeat stale: last success %.0fs ago", age)
                    self._last_heartbeat_stale_warning_at = now

        logger.info("Supervisor stopped")

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                sent = await self._gateway.send_event(
                    WS_EVENTS["ROBOT_HEARTBEAT"], {"robotId": self._robot_id}
                )
                if sent:
                    self.stats.last_heartbeat_at = time.time()
                else:
                    session = self._get_session()
                    headers = {"Content-Type": "application/json"}
                    robot_auth_token = (self.config.server.robot_auth_token_value() or "").strip()
                    if robot_auth_token:
                        headers["Authorization"] = f"Bearer {robot_auth_token}"
                    else:
                        await asyncio.sleep(1)
                        continue
                    async with session.post(
                        f"{self.config.server.api_url}/api/v1/robots/heartbeat",
                        json={},
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status in (200, 201):
                            self.stats.last_heartbeat_at = time.time()

                if time.time() - self._last_telemetry_sent_at >= TELEMETRY_INTERVAL_SEC:
                    await self._send_telemetry()
                    self._last_telemetry_sent_at = time.time()
            except Exception as e:
                logger.debug("Heartbeat error (non-fatal): %s", e)
            await asyncio.sleep(15)

    async def _send_telemetry(self) -> None:
        if not self.config.telemetry.operational_enabled:
            return
        streamer_version: str | None = None
        for runtime in self._camera_runtimes:
            version = runtime.video_profile.botparty_streamer_version()
            if version:
                streamer_version = version
                break
        if not streamer_version:
            version_file = self._repo_root / ".botparty" / "bin" / "botparty-streamer.version"
            try:
                raw = version_file.read_text(encoding="utf-8").strip()
                streamer_version = raw or None
            except Exception:
                streamer_version = None

        payload: dict[str, Any] = {
            "clientVersion": __version__,
            "buildId": __build_id__,
            "gitBranch": self._client_git_branch,
            "gitCommit": self._client_git_commit,
            "gitDirty": self._client_git_dirty,
            "pythonVersion": self._python_version,
            "cpuPercent": self._read_cpu_percent(),
            "memoryPercent": self._read_memory_percent(),
            "temperatureC": self._read_temperature_c(),
            "uptimeSec": self._get_uptime_sec(),
            "controlConnected": self._gateway.connected,
            "livekitConnected": self._livekit_connected,
            "commandsReceived": self.stats.commands_received,
            "cameraFrames": self._total_camera_frames(),
            "controlReconnects": self.stats.reconnect_attempts,
            "mediaReconnects": self.stats.media_reconnects,
            "controlDisconnects": self.stats.control_disconnects,
            "safetyStops": self.stats.safety_stops,
            "watchdogStops": self.stats.watchdog_stops,
            "emergencyStops": self.stats.emergency_stops,
            "commandQueueDrops": self.stats.command_queue_drops,
            "staleCommands": self.stats.stale_commands,
        }
        if streamer_version:
            payload["botpartyStreamerVersion"] = streamer_version
        if self.config.telemetry.product_analytics_enabled:
            payload["productMilestones"] = sorted(self._product_milestones)
        if _PSUTIL_AVAILABLE:
            try:
                payload["cpuPercent"] = float(_psutil.cpu_percent(interval=None))
                payload["memoryPercent"] = float(_psutil.virtual_memory().percent)
                boot_time = float(_psutil.boot_time())
                payload["uptimeSec"] = max(0, int(time.time() - boot_time))
            except Exception:
                pass

        sent = await self._gateway.send_event(WS_EVENTS["ROBOT_TELEMETRY"], payload)
        if not sent:
            session = self._get_session()
            headers = {"Content-Type": "application/json"}
            robot_auth_token = (self.config.server.robot_auth_token_value() or "").strip()
            if robot_auth_token:
                headers["Authorization"] = f"Bearer {robot_auth_token}"
            if not robot_auth_token:
                return
            async with session.post(
                f"{self.config.server.api_url}/api/v1/robots/telemetry",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                await response.content.read(4_096)

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
            self._last_cpu_sample = (values[3], sum(values))
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
            previous = self._last_cpu_sample
            self._last_cpu_sample = (idle, total)

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
        while self._running:
            try:
                if self._gateway.connected:
                    await asyncio.sleep(3)
                    continue

                session = self._get_session()
                headers = {"Content-Type": "application/json"}
                robot_auth_token = (self.config.server.robot_auth_token_value() or "").strip()
                if not robot_auth_token:
                    await asyncio.sleep(3)
                    continue
                headers["Authorization"] = f"Bearer {robot_auth_token}"
                async with session.post(
                    f"{self.config.server.api_url}/api/v1/robots/actions/poll",
                    json={},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
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
        stream = payload.get("stream") if isinstance(payload, dict) else None
        if isinstance(stream, dict):
            next_remote_bitrate = self._remote_target_bitrate_kbps
            if "targetBitrateKbps" in stream:
                next_remote_bitrate = self._parse_target_bitrate_kbps(
                    stream.get("targetBitrateKbps")
                )
            active_camera = stream.get("activeCameraId")
            if isinstance(active_camera, str) and active_camera.strip():
                self._primary_camera_id = active_camera.strip()

            next_effective_bitrate = (
                min(next_remote_bitrate, self._configured_target_bitrate_kbps)
                if next_remote_bitrate is not None
                and self._configured_target_bitrate_kbps is not None
                else next_remote_bitrate
                or self._configured_target_bitrate_kbps
                or self._default_target_bitrate_kbps()
            )
            if (
                next_effective_bitrate != self._effective_target_bitrate_kbps()
                or next_remote_bitrate != self._remote_target_bitrate_kbps
            ):
                self._remote_target_bitrate_kbps = next_remote_bitrate
                logger.info(
                    "Remote stream policy: remoteTargetBitrateKbps=%s "
                    "effectiveTargetBitrateKbps=%d",
                    self._remote_target_bitrate_kbps,
                    self._effective_target_bitrate_kbps(),
                )
                if self._livekit_connected:
                    await self._restart_camera_pipeline("stream policy updated")

        for action in payload.get("actions", []) if isinstance(payload, dict) else []:
            if isinstance(action, dict):
                try:
                    validated_action = RemoteAction.model_validate(action)
                except ValidationError as exc:
                    logger.warning("Rejected invalid remote action: %s", exc)
                    continue
                await self._execute_action(
                    validated_action.model_dump(by_alias=True, exclude_none=True)
                )

    async def _diagnostics_upload_loop(self) -> None:
        while self._running:
            try:
                await self._diagnostics_uploader.upload_once(self._diag_enabled_until)
            except Exception as e:
                logger.debug("Diagnostics upload error (non-fatal): %s", e)
            await asyncio.sleep(2)

    async def _authenticate(self) -> AuthResult | None:
        authenticator = getattr(self, "_authenticator", None)
        if authenticator is None:
            authenticator = ClientAuthenticator(self.config, self._get_session)
            self._authenticator = authenticator
        publish_camera_ids = (
            [runtime.camera_id for runtime in self._camera_runtimes]
            if self._uses_direct_livekit_publisher()
            else []
        )
        outcome = await authenticator.claim(
            publish_camera_ids=publish_camera_ids,
            capabilities=self._capability_manifest,
        )
        if isinstance(outcome, AuthFailure):
            logger.error("Authentication failed (%s): %s", outcome.code, outcome.detail)
            return None
        self._remote_target_bitrate_kbps = outcome.target_bitrate_kbps
        logger.info(
            "Video target bitrate: remote=%s configured=%s effective=%d kbps",
            self._remote_target_bitrate_kbps,
            self._configured_target_bitrate_kbps,
            self._effective_target_bitrate_kbps(),
        )
        return outcome

    def _read_git_metadata(self) -> tuple[str | None, str | None, bool]:
        if not (self._repo_root / ".git").exists():
            return None, None, False

        def read_git_output(args: list[str]) -> str | None:
            try:
                result = subprocess.run(
                    args,
                    cwd=self._repo_root,
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
        if self._update_in_progress:
            logger.info("Client update already in progress - ignoring duplicate action")
            return

        if self._update_manager is None:
            logger.warning("Skipping update_client: signed transactional OTA is not enabled")
            if action_id is not None:
                self._emit_remote_action_result(action_id, "failed", "ota_disabled")
            return

        self._update_in_progress = True
        try:
            await self._trigger_hardware_stop("ota_update")
            config_path = Path(os.getenv("BOTPARTY_CONFIG", "config.yaml")).resolve()
            executable = await asyncio.to_thread(
                self._update_manager.install,
                config_path,
            )
            logger.info("Verified OTA release installed; activating new slot")
            if action_id is not None:
                self._emit_remote_action_result(action_id, "completed", "installed")
            await self._restart_process_after_update(executable)
        except Exception as exc:
            logger.error("Client update failed: %s", exc)
            if action_id is not None:
                self._emit_remote_action_result(action_id, "failed", "update_failed")
        finally:
            self._update_in_progress = False

    async def _restart_process_after_update(self, executable: Path | None = None) -> None:
        logger.info("Restarting client process after successful update")
        self._planned_disconnect_notice_sent = True
        self._livekit_connected = False

        await self._stop_media_tasks()

        room = self._room
        if room is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(room.disconnect(), timeout=5)

        if self._http_session and not self._http_session.closed:
            with contextlib.suppress(Exception):
                await self._http_session.close()

        await self._trigger_hardware_stop("process_restart")

        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._gateway.close(), timeout=2)

        # Brief pause to let the event loop flush any final callbacks
        await asyncio.sleep(0.1)

        target = str(executable or Path(sys.executable))
        os.execv(target, [target, "-m", "botparty_robot", *sys.argv[1:]])
