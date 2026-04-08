"""External publisher manager for direct low-latency video."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Callable, Optional

from .config import RobotConfig

logger = logging.getLogger("botparty.camera")


class LiveKitPublisherManager:
    def __init__(
        self,
        config: RobotConfig,
        video_profile,
        *,
        token_fn: Callable[[], str | None],
        livekit_url_fn: Callable[[], str | None],
        camera_id: str = "front",
    ) -> None:
        self.config = config
        self.video_profile = video_profile
        self.camera_id = camera_id
        self._token_fn = token_fn
        self._livekit_url_fn = livekit_url_fn
        self._audio_task: Optional[asyncio.Task] = None
        self._frame_count = 0
        self._last_reported_frame_count = 0
        self._last_reported_at = 0.0
        self._started_at = 0.0
        self._published_tracks: list[str] = []
        self._source_mime_types: list[str] = []
        self._ffmpeg_progress: dict[str, str] = {}

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def audio_task(self) -> Optional[asyncio.Task]:
        return self._audio_task

    def restart_audio(self, room, running_fn):
        return None

    async def run(
        self,
        room,
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
        self._started_at = time.monotonic()
        self._last_reported_at = self._started_at
        self._last_reported_frame_count = self._frame_count

        try:
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
                    raise RuntimeError(f"publisher exited with code {return_code}")
                self._log_runtime_stats_if_due()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            self._log_exit_summary()
            if log_task is not None:
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

    async def _drain_logs(self, stream) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            message = line.decode("utf-8", errors="replace").strip()
            if message:
                self._handle_log_line(message)

    def _handle_log_line(self, message: str) -> None:
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
            logger.info("Direct runtime %s: ffmpeg_fps=%.1f", self.camera_id, parsed_fps)
            return

        source_match = re.search(r'found source\s+\{"mimeType":\s*"([^"]+)"\}', message)
        if source_match:
            mime = source_match.group(1)
            if mime not in self._source_mime_types:
                self._source_mime_types.append(mime)
            logger.info("Direct source %s: %s", self.camera_id, mime)
            return

        track_match = re.search(r'published track\s+\{"name":\s*"[^"]*",\s*"source":\s*"([^"]+)"', message)
        if track_match:
            source = track_match.group(1)
            self._published_tracks.append(source)
            logger.info("Direct track published %s: %s", self.camera_id, source.lower())
            return

        noisy_debug_patterns = (
            "handling subscribed quality update",
            "subscriber requested backup codec but no track found",
            "deprecated pixel format used, make sure you did set range correctly",
        )
        if any(pattern in message for pattern in noisy_debug_patterns):
            logger.debug("video: %s", message)
            return

        logger.warning("video: %s", message)

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

        parts = [f"camera={self.camera_id}"]
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

        label = "Direct ffmpeg final" if progress_value == "end" else "Direct ffmpeg"
        logger.info("%s: %s", label, " ".join(parts))

    def _log_runtime_stats_if_due(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_reported_at
        if elapsed < 10:
            return
        if self._frame_count > 0:
            delta_frames = self._frame_count - self._last_reported_frame_count
            sent_fps = (delta_frames / elapsed) if elapsed > 0 else 0.0
            logger.info(
                "Direct runtime: camera=%s sent_fps=%.1f frames=%d uptime=%.1fs",
                self.camera_id,
                sent_fps,
                self._frame_count,
                now - self._started_at,
            )
        else:
            logger.info(
                "Direct runtime: camera=%s uptime=%.1fs tracks=%s sources=%s",
                self.camera_id,
                now - self._started_at,
                ",".join(self._published_tracks) if self._published_tracks else "none",
                ",".join(self._source_mime_types) if self._source_mime_types else "none",
            )
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
