"""Operational helpers for BotPartyClient."""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import aiohttp

from . import __version__
from .client_state import LOCAL_GIT_STATUS_IGNORE_PATHS, TELEMETRY_INTERVAL_SEC, logger

try:
    import psutil as _psutil  # type: ignore
    _PSUTIL_AVAILABLE = True
except Exception:
    _psutil = None  # type: ignore
    _PSUTIL_AVAILABLE = False


@dataclass(slots=True)
class AuthResult:
    token: str | None
    robot_id: str | None
    livekit_url: str | None
    ingress: dict[str, object] | None
    publish_tokens: dict[str, str]
    robot_auth_token: str | None


class ClientOpsMixin:
    def _env_bool(self, name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def _allowed_update_remotes(self) -> list[str]:
        raw = os.getenv("BOTPARTY_CLIENT_UPDATE_ALLOWED_REMOTES", "").strip()
        if not raw:
            return [
                "https://github.com/onlybrezel/botparty-client.git",
                "git@github.com:onlybrezel/botparty-client.git",
            ]
        return [entry.strip() for entry in raw.split(",") if entry.strip()]

    def _is_allowed_update_remote(self, remote_url: str) -> bool:
        allowed = self._allowed_update_remotes()
        if not allowed:
            return False
        return any(remote_url == entry for entry in allowed)

    async def _read_git_head_commit(self) -> str | None:
        process = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        if not stdout:
            return None
        head = stdout.decode("utf-8", errors="replace").strip()
        return head or None

    def _mask_remote_url(self, value: str) -> str:
        if "://" not in value or "@" not in value:
            return value

        scheme, rest = value.split("://", 1)
        credentials, tail = rest.split("@", 1)
        if not credentials:
            return value
        return f"{scheme}://***@{tail}"

    def _sanitize_update_log_line(self, line: str) -> str:
        remote_url = os.getenv("BOTPARTY_CLIENT_UPDATE_REMOTE_URL", "").strip()
        if not remote_url:
            return line

        return line.replace(remote_url, self._mask_remote_url(remote_url))

    def _build_git_pull_argv(self) -> list[str]:
        """Build git pull argv, optionally using an authenticated remote URL from env."""
        remote_url = os.getenv("BOTPARTY_CLIENT_UPDATE_REMOTE_URL", "").strip()
        remote_ref = os.getenv("BOTPARTY_CLIENT_UPDATE_REMOTE_REF", "").strip()
        if not remote_url:
            return ["git", "pull", "--ff-only"]

        argv = ["git", "pull", "--ff-only", remote_url]
        if remote_ref:
            argv.append(remote_ref)
        return argv

    async def _supervisor(self) -> None:
        logger.info("Supervisor started")
        timeout_sec = self.config.safety.max_run_time_ms / 1000.0

        while self._running:
            await asyncio.sleep(5)

            for runtime in self._camera_runtimes:
                task = runtime.task
                if task and task.done():
                    exc = task.exception() if not task.cancelled() else None
                    if exc:
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
                            logger.error("Camera %s restarted 5 times - giving up", runtime.camera_id)

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
                        logger.warning("Audio task died - restarting (%s): %s", runtime.camera_id, exc)
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

            if self.stats.last_command_at > 0:
                elapsed = time.time() - self.stats.last_command_at
                if elapsed > timeout_sec:
                    logger.info("Command timeout (%.0fs) - auto-stopping motors", elapsed)
                    await self._trigger_hardware_stop("command_timeout")
                    self.stats.last_command_at = 0

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
                    "robot:heartbeat", {"robotId": self._robot_id}
                )
                if sent:
                    self.stats.last_heartbeat_at = time.time()
                else:
                    session = self._get_session()
                    headers = {"Content-Type": "application/json"}
                    robot_auth_token = (self.config.server.robot_auth_token or "").strip()
                    if robot_auth_token:
                        headers["Authorization"] = f"Bearer {robot_auth_token}"
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
        }
        if streamer_version:
            payload["botpartyStreamerVersion"] = streamer_version
        if _PSUTIL_AVAILABLE:
            try:
                payload["cpuPercent"] = float(_psutil.cpu_percent(interval=None))
                payload["memoryPercent"] = float(_psutil.virtual_memory().percent)
                boot_time = float(_psutil.boot_time())
                payload["uptimeSec"] = max(0, int(time.time() - boot_time))
            except Exception:
                pass

        sent = await self._gateway.send_event("robot:telemetry", payload)
        if not sent:
            session = self._get_session()
            headers = {"Content-Type": "application/json"}
            robot_auth_token = (self.config.server.robot_auth_token or "").strip()
            if robot_auth_token:
                headers["Authorization"] = f"Bearer {robot_auth_token}"
            await session.post(
                f"{self.config.server.api_url}/api/v1/robots/telemetry",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            )

    def _read_temperature_c(self) -> Optional[float]:
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

    def _get_uptime_sec(self) -> Optional[int]:
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

    def _read_cpu_percent(self) -> Optional[float]:
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

    def _read_memory_percent(self) -> Optional[float]:
        try:
            meminfo: dict[str, int] = {}
            with open("/proc/meminfo", "r", encoding="utf-8") as handle:
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
                robot_auth_token = (self.config.server.robot_auth_token or "").strip()
                if robot_auth_token:
                    headers["Authorization"] = f"Bearer {robot_auth_token}"
                async with session.post(
                    f"{self.config.server.api_url}/api/v1/robots/actions/poll",
                    json={},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
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
                next_remote_bitrate = self._parse_target_bitrate_kbps(stream.get("targetBitrateKbps"))
            active_camera = stream.get("activeCameraId")
            if isinstance(active_camera, str) and active_camera.strip():
                self._primary_camera_id = active_camera.strip()
                self._sync_primary_runtime_aliases()

            next_effective_bitrate = (
                max(next_remote_bitrate, self._configured_target_bitrate_kbps)
                if next_remote_bitrate is not None and self._configured_target_bitrate_kbps is not None
                else next_remote_bitrate
                or self._configured_target_bitrate_kbps
                or self._default_target_bitrate_kbps()
            )
            if next_effective_bitrate != self._effective_target_bitrate_kbps() or next_remote_bitrate != self._remote_target_bitrate_kbps:
                self._remote_target_bitrate_kbps = next_remote_bitrate
                logger.info(
                    "Remote stream policy: remoteTargetBitrateKbps=%s effectiveTargetBitrateKbps=%d",
                    self._remote_target_bitrate_kbps,
                    self._effective_target_bitrate_kbps(),
                )
                if self._livekit_connected:
                    await self._restart_camera_pipeline("stream policy updated")

        for action in payload.get("actions", []) if isinstance(payload, dict) else []:
            if isinstance(action, dict):
                await self._execute_action(action)

    async def _diagnostics_upload_loop(self) -> None:
        while self._running:
            try:
                if time.time() < self._diag_enabled_until:
                    lines = list(self._diag_buffer)
                    if self._diag_last_sent_idx >= len(lines):
                        self._diag_last_sent_idx = 0
                    if self._diag_last_sent_idx < len(lines):
                        batch = lines[self._diag_last_sent_idx:self._diag_last_sent_idx + 50]
                        self._diag_last_sent_idx += len(batch)
                        session = self._get_session()
                        headers = {"Content-Type": "application/json"}
                        robot_auth_token = (self.config.server.robot_auth_token or "").strip()
                        if robot_auth_token:
                            headers["Authorization"] = f"Bearer {robot_auth_token}"
                        await session.post(
                            f"{self.config.server.api_url}/api/v1/robots/logs",
                            json={
                                "lines": batch,
                            },
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=5),
                        )
            except Exception as e:
                logger.debug("Diagnostics upload error (non-fatal): %s", e)
            await asyncio.sleep(2)

    async def _authenticate(
        self,
    ) -> AuthResult:
        try:
            publish_camera_ids = (
                [runtime.camera_id for runtime in self._camera_runtimes]
                if self._uses_direct_livekit_publisher()
                else []
            )
            session = self._get_session()
            async with session.post(
                f"{self.config.server.api_url}/api/v1/robots/claim",
                json={
                    "claimToken": self.config.server.claim_token,
                    "deviceKey": self.config.server.device_key,
                    "publishCameraIds": publish_camera_ids,
                },
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as resp:
                if resp.status in (301, 302, 307, 308):
                    location = resp.headers.get("Location", "")
                    logger.error(
                        "Server redirected (%d) to: %s - check api_url (http vs https)",
                        resp.status,
                        location,
                    )
                    return AuthResult(None, None, None, None, {}, None)
                if resp.status not in (200, 201):
                    text = await resp.text()
                    logger.error("Claim failed (%d): %s", resp.status, text)
                    if resp.status == 404 and self.config.server.api_url.startswith("http://"):
                        logger.error("Hint: try https:// if your server has SSL enabled")
                        return AuthResult(None, None, None, None, {}, None)

                data = await resp.json()
                stream = data.get("stream") if isinstance(data, dict) else None
                if isinstance(stream, dict):
                    self._remote_target_bitrate_kbps = self._parse_target_bitrate_kbps(
                        stream.get("targetBitrateKbps")
                    )
                else:
                    self._remote_target_bitrate_kbps = None

                logger.info(
                    "Video target bitrate: remote=%s configured=%s effective=%d kbps",
                    self._remote_target_bitrate_kbps,
                    self._configured_target_bitrate_kbps,
                    self._effective_target_bitrate_kbps(),
                )

                livekit_url = data.get("livekitUrl")
                if not isinstance(livekit_url, str):
                    livekit_url = None
                ingress = data.get("ingress")
                if not isinstance(ingress, dict):
                    ingress = None
                publish_tokens_raw = data.get("publishTokens")
                publish_tokens = (
                    {
                        str(key).strip(): str(value).strip()
                        for key, value in publish_tokens_raw.items()
                        if str(key).strip() and isinstance(value, str) and value.strip()
                    }
                    if isinstance(publish_tokens_raw, dict)
                    else {}
                )
                robot_auth_token = data.get("robotAuthToken")
                return AuthResult(
                    data.get("token"),
                    data.get("robotId"),
                    livekit_url,
                    ingress,
                    publish_tokens,
                    robot_auth_token.strip() if isinstance(robot_auth_token, str) and robot_auth_token.strip() else None,
                )
        except Exception as e:
            logger.error("Authentication error: %s", e)
            return AuthResult(None, None, None, None, {}, None)

    def _read_git_metadata(self) -> tuple[Optional[str], Optional[str], bool]:
        if not (self._repo_root / ".git").exists():
            return None, None, False

        def read_git_output(args: list[str]) -> Optional[str]:
            try:
                result = subprocess.run(
                    args,
                    cwd=self._repo_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
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

    async def _run_update_command(self, argv: list[str], label: str) -> None:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(self._repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await process.communicate()
        output = stdout.decode("utf-8", errors="replace").strip() if stdout else ""
        if output:
            for line in output.splitlines()[-20:]:
                logger.info("%s: %s", label, self._sanitize_update_log_line(line))
        if process.returncode != 0:
            raise RuntimeError(f"{label} failed with exit code {process.returncode}")

    async def _perform_client_update(self) -> None:
        if self._update_in_progress:
            logger.info("Client update already in progress - ignoring duplicate action")
            return

        if self._env_bool("BOTPARTY_DISABLE_REMOTE_UPDATE", False):
            logger.warning("Skipping update_client: remote updates disabled by BOTPARTY_DISABLE_REMOTE_UPDATE")
            return

        if not (self._repo_root / ".git").exists():
            logger.warning("Skipping update_client: repository is not a git checkout at %s", self._repo_root)
            return

        configured_remote = os.getenv("BOTPARTY_CLIENT_UPDATE_REMOTE_URL", "").strip()
        if configured_remote and not self._is_allowed_update_remote(configured_remote):
            logger.error(
                "Skipping update_client: BOTPARTY_CLIENT_UPDATE_REMOTE_URL is not allowlisted (%s)",
                self._mask_remote_url(configured_remote),
            )
            return

        self._update_in_progress = True
        snapshot_commit: str | None = None
        try:
            snapshot_commit = await self._read_git_head_commit()

            await self._run_update_command(self._build_git_pull_argv(), "git pull --ff-only")
            if self._env_bool("BOTPARTY_CLIENT_UPDATE_VERIFY_SIGNATURE", True):
                await self._run_update_command(
                    ["git", "verify-commit", "HEAD"],
                    "git verify-commit HEAD",
                )
            await self._run_update_command(
                [sys.executable, "-m", "pip", "install", "-e", ".[all]"],
                "pip install -e .[all]",
            )
            (
                self._client_git_branch,
                self._client_git_commit,
                self._client_git_dirty,
            ) = self._read_git_metadata()
            logger.info(
                "Client update complete: version=%s branch=%s commit=%s dirty=%s",
                __version__,
                self._client_git_branch or "-",
                self._client_git_commit or "-",
                self._client_git_dirty,
            )
            await self._restart_process_after_update()
        except Exception as exc:
            logger.error("Client update failed: %s", exc)
            if snapshot_commit:
                with contextlib.suppress(Exception):
                    await self._run_update_command(
                        ["git", "reset", "--hard", snapshot_commit],
                        f"git reset --hard {snapshot_commit[:12]}",
                    )
                    logger.warning("Rolled back failed client update to snapshot %s", snapshot_commit[:12])
        finally:
            self._update_in_progress = False

    async def _restart_process_after_update(self) -> None:
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

        os.execv(sys.executable, [sys.executable, "-m", "botparty_robot"])
