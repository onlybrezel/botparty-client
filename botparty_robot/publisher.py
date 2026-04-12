"""External publisher manager for direct low-latency video."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from typing import Callable, Optional

from livekit import rtc

from .config import RobotConfig

logger = logging.getLogger("botparty.camera")


class LiveKitPublisherManager:
    def __init__(
        self,
        config: RobotConfig,
        video_profile,
        *,
        token_fn: Callable[[], str | None],
        audio_token_fn: Callable[[], str | None],
        livekit_url_fn: Callable[[], str | None],
        camera_id: str = "front",
        audio_enabled: bool = False,
    ) -> None:
        self.config = config
        self.video_profile = video_profile
        self.camera_id = camera_id
        self._token_fn = token_fn
        self._audio_token_fn = audio_token_fn
        self._livekit_url_fn = livekit_url_fn
        self._audio_enabled = audio_enabled
        self._audio_task: Optional[asyncio.Task] = None
        self._audio_room: rtc.Room | None = None
        self._frame_count = 0
        self._last_reported_frame_count = 0
        self._last_reported_at = 0.0
        self._started_at = 0.0
        self._last_ffmpeg_progress_at = 0.0
        self._published_tracks: list[str] = []
        self._source_mime_types: list[str] = []
        self._ffmpeg_progress: dict[str, str] = {}
        self._recent_log_lines: deque[str] = deque(maxlen=40)

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def audio_task(self) -> Optional[asyncio.Task]:
        return self._audio_task

    def restart_audio(self, room, running_fn):
        if not self._audio_enabled or not self.video_profile.has_audio() or self._audio_room is None:
            return None
        task = asyncio.create_task(self.video_profile.start_audio(rtc, self._audio_room, running_fn))
        self._audio_task = task
        return task

    async def run(
        self,
        room,
        target_bitrate_kbps: Optional[int],
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        fallback_attempted = False
        audio_fallback_attempted = False
        codec_fallback_attempted = False
        while True:
            try:
                await self._run_once(target_bitrate_kbps, running_fn, connected_fn)
                return
            except RuntimeError as exc:
                uptime = max(0.0, time.monotonic() - self._started_at)
                if self._should_retry_without_direct_audio(exc, uptime, audio_fallback_attempted):
                    audio_fallback_attempted = True
                    self.video_profile.options["direct_audio_enabled"] = False
                    logger.warning(
                        "Direct publisher exited after %.1fs with inline audio; retrying without direct audio branch to keep ffmpeg video backend",
                        uptime,
                    )
                    continue
                if self._should_retry_with_libx264(exc, uptime, codec_fallback_attempted):
                    codec_fallback_attempted = True
                    previous_codec = str(
                        self.video_profile.options.get("video_codec")
                        or self.video_profile.detect_default_h264_codec()
                    ).strip()
                    self.video_profile.options["video_codec"] = "libx264"
                    logger.warning(
                        "Direct publisher exited after %.1fs on ffmpeg backend (codec=%s); retrying with codec=libx264",
                        uptime,
                        previous_codec,
                    )
                    continue
                if self._should_retry_with_gstreamer(exc, uptime, fallback_attempted):
                    fallback_attempted = True
                    previous_backend = self._selected_publish_backend()
                    self.video_profile.options["publish_backend"] = "gstreamer"
                    logger.warning(
                        "Direct publisher exited after %.1fs on %s backend; retrying with pure gstreamer backend",
                        uptime,
                        previous_backend,
                    )
                    continue
                raise

    async def _run_once(
        self,
        target_bitrate_kbps: Optional[int],
        running_fn: Callable[[], bool],
        connected_fn: Callable[[], bool],
    ) -> None:
        livekit_url = str(self._livekit_url_fn() or "").strip()
        token = str(self._token_fn() or "").strip()
        if not livekit_url or not token:
            raise RuntimeError("LiveKit publish URL or token is missing")

        proc = None
        log_task = None
        audio_task = None
        audio_room = None
        audio_done_logged = False
        self._published_tracks.clear()
        self._source_mime_types.clear()
        self._ffmpeg_progress.clear()
        self._recent_log_lines.clear()
        self._started_at = time.monotonic()
        self._last_reported_at = self._started_at
        self._last_reported_frame_count = self._frame_count

        try:
            if self._audio_enabled and self.video_profile.has_audio():
                audio_token = str(self._audio_token_fn() or "").strip()
                if not audio_token:
                    logger.warning(
                        "Direct audio requested for %s but no base publish token is available; skipping audio",
                        self.camera_id,
                    )
                elif audio_token == token:
                    logger.warning(
                        "Direct audio skipped for %s because audio token equals camera publish token; "
                        "this would cause participant SID collisions",
                        self.camera_id,
                    )
                else:
                    try:
                        audio_room = rtc.Room()
                        await audio_room.connect(livekit_url, audio_token)
                        self._audio_room = audio_room
                        audio_task = asyncio.create_task(
                            self.video_profile.start_audio(rtc, audio_room, running_fn)
                        )
                        self._audio_task = audio_task
                        logger.info("Direct audio started for camera=%s", self.camera_id)
                    except Exception as audio_exc:
                        logger.warning(
                            "Direct audio setup failed for %s; continuing with video only: %s",
                            self.camera_id,
                            audio_exc,
                        )
                        if audio_room is not None:
                            with contextlib.suppress(Exception):
                                await audio_room.disconnect()
                        audio_room = None
                        self._audio_room = None
                        self._audio_task = None

            proc = await self.video_profile.spawn_livekit_process(
                livekit_url=livekit_url,
                token=token,
                target_bitrate_kbps=target_bitrate_kbps,
            )
            logger.info(
                "Publishing %s directly: device=%s resolution=%dx%d fps=%d publish_fps=%.1f",
                self.camera_id,
                self.config.camera.device,
                self.config.camera.width,
                self.config.camera.height,
                self.config.camera.fps,
                self.video_profile.output_fps(),
            )

            log_stream = proc.stderr or proc.stdout
            if log_stream is not None:
                log_task = asyncio.create_task(self._drain_logs(log_stream))

            while running_fn() and connected_fn():
                return_code = proc.returncode
                if return_code is not None:
                    if return_code == 0:
                        return
                    raise RuntimeError(self._format_nonzero_exit(return_code))
                if audio_task is not None and audio_task.done() and not audio_done_logged:
                    audio_done_logged = True
                    audio_error = audio_task.exception() if not audio_task.cancelled() else None
                    if audio_error is not None:
                        logger.warning("Direct audio task failed for %s: %s", self.camera_id, audio_error)
                    else:
                        logger.warning("Direct audio task stopped for %s", self.camera_id)
                self._log_runtime_stats_if_due()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self._log_exit_summary()
            if log_task is not None:
                if proc is not None and proc.returncode is not None:
                    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                        await asyncio.wait_for(log_task, timeout=0.5)
                if not log_task.done():
                    log_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await log_task
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logger.warning("publisher did not exit after SIGTERM; killing")
                    with contextlib.suppress(ProcessLookupError):
                        proc.kill()
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(proc.wait(), timeout=2)
            if audio_task is not None and not audio_task.done():
                audio_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await audio_task
            self._audio_task = None
            if audio_room is not None:
                with contextlib.suppress(Exception):
                    await audio_room.disconnect()
            self._audio_room = None

    def _selected_publish_backend(self) -> str:
        preferred = str(
            self.video_profile.options.get("publish_backend")
            or self.video_profile.options.get("livekit_publish_backend")
            or "auto"
        ).strip().lower()
        return preferred or "auto"

    def _should_retry_with_gstreamer(
        self,
        error: RuntimeError,
        uptime_sec: float,
        fallback_attempted: bool,
    ) -> bool:
        if fallback_attempted:
            return False
        if self._selected_publish_backend() == "gstreamer":
            return False
        if uptime_sec > 8.0:
            return False

        message = str(error).lower()
        non_retryable = (
            "missing",
            "not installed",
            "token",
            "permission denied",
        )
        if any(marker in message for marker in non_retryable):
            return False
        return True

    def _should_retry_without_direct_audio(
        self,
        error: RuntimeError,
        uptime_sec: float,
        fallback_attempted: bool,
    ) -> bool:
        if fallback_attempted:
            return False
        if uptime_sec > 8.0:
            return False
        if getattr(self.video_profile, "profile_name", "") != "gstreamer_arecord":
            return False

        direct_audio_enabled = self.video_profile.options.get("direct_audio_enabled", True)
        if not bool(direct_audio_enabled):
            return False

        message = str(error).lower()
        non_retryable = (
            "missing",
            "not installed",
            "token",
            "permission denied",
        )
        if any(marker in message for marker in non_retryable):
            return False
        return True

    def _should_retry_with_libx264(
        self,
        error: RuntimeError,
        uptime_sec: float,
        fallback_attempted: bool,
    ) -> bool:
        if fallback_attempted:
            return False
        if self._selected_publish_backend() == "gstreamer":
            return False
        if uptime_sec > 8.0:
            return False

        configured_codec = str(
            self.video_profile.options.get("video_codec")
            or self.video_profile.detect_default_h264_codec()
        ).strip().lower()
        if configured_codec == "libx264":
            return False

        message = str(error).lower()
        non_retryable = (
            "missing",
            "not installed",
            "token",
            "permission denied",
        )
        if any(marker in message for marker in non_retryable):
            return False
        return True

    async def _drain_logs(self, stream) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                self._handle_log_line(message)

    def _handle_log_line(self, message: str) -> None:
        self._recent_log_lines.append(message)
        progress_pair = self._parse_ffmpeg_progress_pair(message)
        if progress_pair is not None:
            key, value = progress_pair
            self._ffmpeg_progress[key] = value
            if key == "frame":
                with contextlib.suppress(ValueError):
                    self._frame_count = max(self._frame_count, int(value))
            if key == "progress":
                self._log_ffmpeg_progress(value)
            return

        parsed_frame = self._parse_ffmpeg_progress_int(message, "frame")
        if parsed_frame is not None:
            self._frame_count = max(self._frame_count, parsed_frame)
            return

        parsed_fps = self._parse_ffmpeg_progress_float(message, "fps")
        if parsed_fps is not None:
            return

        source_match = re.search(r'found source\s+\{"mimeType":\s*"([^"]+)"\}', message)
        if source_match:
            mime = source_match.group(1)
            if mime not in self._source_mime_types:
                self._source_mime_types.append(mime)
            logger.info("Direct source %s: %s", self.camera_id, mime)
            return

        # Go slog format: "msg"="published track" "name"="camera" "source"="CAMERA"
        slog_track_match = re.search(r'"msg"="published track"', message)
        if slog_track_match:
            name_m = re.search(r'"name"="([^"]*)"', message)
            src_m = re.search(r'"source"="([^"]*)"', message)
            name = name_m.group(1) if name_m else "?"
            src = src_m.group(1).lower() if src_m else "?"
            self._published_tracks.append(src)
            logger.info("Direct track published %s: name=%s source=%s", self.camera_id, name, src)
            return

        # Old JSON format: published track {"name": "...", "source": "..."}
        json_track_match = re.search(r'published track\s+\{"name":\s*"[^"]*",\s*"source":\s*"([^"]+)"', message)
        if json_track_match:
            source = json_track_match.group(1)
            self._published_tracks.append(source)
            logger.info("Direct track published %s: %s", self.camera_id, source.lower())
            return

        # Go slog messages that are noisy internal SDK events → suppress to debug
        noisy_debug_slog = (
            '"msg"="participant sid update"',
            '"msg"="signal reconnecting"',
            '"msg"="signal reconnected"',
            '"msg"="starting ICE restart"',
            '"msg"="ICE restart completed"',
        )
        if any(pattern in message for pattern in noisy_debug_slog):
            logger.debug("video: %s", message)
            return

        noisy_debug_patterns = (
            "handling subscribed quality update",
            "subscriber requested backup codec but no track found",
            "deprecated pixel format used, make sure you did set range correctly",
        )
        if any(pattern in message for pattern in noisy_debug_patterns):
            logger.debug("video: %s", message)
            return

        # For Go slog-format lines, use the "level"=N field to determine log level.
        # slog levels: <=0 = INFO, 4 = WARN, 8 = ERROR; negative = DEBUG.
        slog_level_match = re.search(r'"level"=(-?\d+)', message)
        if slog_level_match:
            level_num = int(slog_level_match.group(1))
            if level_num >= 8:
                logger.error("video: %s", message)
            elif level_num >= 4:
                logger.warning("video: %s", message)
            elif level_num >= 0:
                logger.info("video: %s", message)
            else:
                logger.debug("video: %s", message)
            return

        # Unknown lines: only escalate to WARNING when they look like errors.
        error_keywords = ("error", "failed", "fatal", "panic", "exception")
        if any(kw in message.lower() for kw in error_keywords):
            logger.warning("video: %s", message)
        else:
            logger.info("video: %s", message)

    def _format_nonzero_exit(self, return_code: int) -> str:
        if not self._recent_log_lines:
            return f"publisher exited with code {return_code}"

        detail_lines = [
            line
            for line in self._recent_log_lines
            if re.search(r"error|failed|fatal|panic|exited", line, flags=re.IGNORECASE)
        ]
        if not detail_lines:
            detail_lines = list(self._recent_log_lines)[-2:]

        details = " | ".join(detail_lines[-3:])
        return f"publisher exited with code {return_code}: {details}"

    def _parse_ffmpeg_progress_pair(self, message: str) -> tuple[str, str] | None:
        if "=" not in message:
            return None
        key, value = message.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            return None
        progress_keys = {
            "frame",
            "fps",
            "stream_0_0_q",
            "bitrate",
            "total_size",
            "out_time_us",
            "out_time_ms",
            "out_time",
            "dup_frames",
            "drop_frames",
            "speed",
            "progress",
        }
        if key not in progress_keys:
            return None
        return key, value

    def _parse_ffmpeg_progress_int(self, message: str, key: str) -> int | None:
        match = re.fullmatch(rf"{re.escape(key)}=(\d+)", message)
        if not match:
            return None
        return int(match.group(1))

    def _parse_ffmpeg_progress_float(self, message: str, key: str) -> float | None:
        match = re.fullmatch(rf"{re.escape(key)}=([0-9]+(?:\.[0-9]+)?)", message)
        if not match:
            return None
        return float(match.group(1))

    def _log_ffmpeg_progress(self, progress_value: str) -> None:
        frame_value = self._ffmpeg_progress.get("frame")
        fps_value = self._ffmpeg_progress.get("fps")
        bitrate_value = self._ffmpeg_progress.get("bitrate")
        speed_value = self._ffmpeg_progress.get("speed")
        out_time_value = self._ffmpeg_progress.get("out_time")
        drop_value = self._ffmpeg_progress.get("drop_frames")
        dup_value = self._ffmpeg_progress.get("dup_frames")

        now = time.monotonic()
        self._last_ffmpeg_progress_at = now

        parts = [f"camera={self.camera_id}"]

        # Inline sent_fps + uptime so we get one combined line instead of two.
        if self._frame_count > 0 and self._last_reported_at > 0:
            elapsed = now - self._last_reported_at
            if elapsed > 0:
                delta = self._frame_count - self._last_reported_frame_count
                parts.append(f"sent_fps={delta / elapsed:.1f}")
        uptime = now - self._started_at if self._started_at > 0 else 0.0
        if uptime >= 1.0:
            parts.append(f"uptime={uptime:.0f}s")
        self._last_reported_at = now
        self._last_reported_frame_count = self._frame_count

        if fps_value:
            parts.append(f"ffmpeg_fps={fps_value}")
        if frame_value:
            parts.append(f"frames={frame_value}")
        if bitrate_value:
            parts.append(f"bitrate={bitrate_value}")
        if speed_value:
            parts.append(f"speed={speed_value}")
        if out_time_value:
            parts.append(f"media_time={out_time_value}")
        if drop_value and drop_value != "0":
            parts.append(f"dropped={drop_value}")
        if dup_value and dup_value != "0":
            parts.append(f"dup={dup_value}")

        label = "Direct final" if progress_value == "end" else "Direct"
        logger.info("%s: %s", label, " ".join(parts))

    def _log_runtime_stats_if_due(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_reported_at
        if elapsed < 10:
            return
        # Skip if ffmpeg progress events are coming in — they already log combined stats.
        if self._last_ffmpeg_progress_at > 0 and now - self._last_ffmpeg_progress_at < elapsed * 1.5:
            self._last_reported_at = now
            self._last_reported_frame_count = self._frame_count
            return
        progress_fps = self._ffmpeg_progress.get("fps")
        progress_bitrate = self._ffmpeg_progress.get("bitrate")
        if self._frame_count > 0:
            delta_frames = self._frame_count - self._last_reported_frame_count
            sent_fps = (delta_frames / elapsed) if elapsed > 0 else 0.0
            message = (
                f"Direct: camera={self.camera_id} sent_fps={sent_fps:.1f} "
                f"uptime={now - self._started_at:.0f}s"
            )
            if progress_fps:
                message += f" ffmpeg_fps={progress_fps}"
            if progress_bitrate:
                message += f" bitrate={progress_bitrate}"
            message += f" frames={self._frame_count}"
            logger.info(message)
        else:
            message = (
                f"Direct: camera={self.camera_id} uptime={now - self._started_at:.0f}s "
                f"tracks={','.join(self._published_tracks) if self._published_tracks else 'none'}"
            )
            if progress_fps:
                message += f" ffmpeg_fps={progress_fps}"
            if not progress_fps and not progress_bitrate:
                message += " ffmpeg=warming_up"
            logger.info(message)
        self._last_reported_at = now
        self._last_reported_frame_count = self._frame_count

    def _log_exit_summary(self) -> None:
        if self._started_at <= 0:
            return
        uptime = max(0.0, time.monotonic() - self._started_at)
        tracks = ",".join(self._published_tracks) if self._published_tracks else "none"
        mimes = ",".join(self._source_mime_types) if self._source_mime_types else "none"
        if self._frame_count > 0:
            logger.info(
                "Direct publisher stopped: camera=%s uptime=%.1fs frames=%d tracks=%s sources=%s",
                self.camera_id,
                uptime,
                self._frame_count,
                tracks,
                mimes,
            )
            return
        logger.info(
            "Direct publisher stopped: camera=%s uptime=%.1fs tracks=%s sources=%s",
            self.camera_id,
            uptime,
            tracks,
            mimes,
        )
