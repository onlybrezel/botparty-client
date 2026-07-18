"""FFmpeg-backed video profile."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from ..config import RobotConfig
from ..process_group import ManagedProcessGroup, credential_minimized_environment
from .base import BaseVideoProfile

FFMPEG_INPUT_FORMAT_MAP = {
    "MJPG": "mjpeg",
    "YUYV": "yuyv422",
}

logger = logging.getLogger("botparty.video.ffmpeg")


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg"

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self._direct_profile: BaseVideoProfile | None = None
        self._streamer_binary_path: str | None = None
        self._installed_streamer_version: str | None = None
        self._maybe_enable_direct_publisher()

    def _resolve_streamer_binary_path(self) -> str | None:
        explicit = (
            self.options.get("publisher_binary")
            or self.options.get("botparty_streamer_path")
            or self.options.get("lk_h264_publisher_path")
        )
        if explicit:
            return str(explicit)

        managed = self.managed_streamer_binary_path()
        if managed.is_file() and os.access(managed, os.X_OK):
            return str(managed)

        return None

    def _verify_streamer_binary(self, binary_path: str) -> bool:
        expected = self.options.get("publisher_binary_sha256")
        try:
            from ..artifacts import verify_installed_streamer

            verify_installed_streamer(
                self.managed_streamer_binary_path()
                if os.path.abspath(binary_path)
                == os.path.abspath(self.managed_streamer_binary_path())
                else Path(binary_path),
                str(expected) if isinstance(expected, str) and expected.strip() else None,
            )
        except Exception as exc:
            logger.warning("Ignoring unverified botparty-streamer binary %s: %s", binary_path, exc)
            return False
        return True

    def _maybe_enable_direct_publisher(self) -> None:
        streamer_binary = self._resolve_streamer_binary_path()
        verified = bool(streamer_binary and self._verify_streamer_binary(streamer_binary))

        if not verified or not streamer_binary:
            logger.info("Verified botparty-streamer unavailable; using legacy ffmpeg SDK transport")
            return

        self._installed_streamer_version = self.read_streamer_version_for_binary(streamer_binary)

        self._streamer_binary_path = streamer_binary
        self.options["publisher_binary"] = streamer_binary

        from .botparty_streamer import VideoProfile as BotPartyStreamerProfile

        self._direct_profile = BotPartyStreamerProfile(self.config)
        logger.info(
            "botparty-streamer direct transport active: version=%s binary=%s",
            self._installed_streamer_version or "unknown",
            streamer_binary,
        )

    def capture_mode(self) -> str:
        if self._direct_profile is not None:
            return self._direct_profile.capture_mode()
        return "ffmpeg"

    def publish_transport(self) -> str:
        if self._direct_profile is not None:
            return self._direct_profile.publish_transport()
        return super().publish_transport()

    def botparty_streamer_version(self) -> str | None:
        return self._installed_streamer_version

    async def spawn_livekit_process(
        self,
        *,
        livekit_url: str,
        token: str,
        target_bitrate_kbps: int | None,
    ) -> asyncio.subprocess.Process | ManagedProcessGroup:
        if self._direct_profile is None:
            raise RuntimeError("Direct publisher is not enabled for this ffmpeg profile")
        return await self._direct_profile.spawn_livekit_process(
            livekit_url=livekit_url,
            token=token,
            target_bitrate_kbps=target_bitrate_kbps,
        )

    async def spawn_ffmpeg_process(self) -> asyncio.subprocess.Process:
        configured_input_format = str(self.options.get("input_format", "")).strip().lower()
        fourcc = (self.camera.fourcc or "").strip().upper()
        output_fps = max(1, round(self.output_fps()))

        input_format: str | None = None
        if configured_input_format and configured_input_format != "auto":
            input_format = configured_input_format
        else:
            mapped_format = FFMPEG_INPUT_FORMAT_MAP.get(fourcc)
            if mapped_format is not None:
                input_format = mapped_format

        cmd = [
            self.options.get("ffmpeg_path", "ffmpeg"),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            self.options.get("loglevel", "error"),
            "-avioflags",
            "direct",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            str(self.options.get("analyzeduration", 0)),
            "-probesize",
            str(self.options.get("probesize", 32)),
            "-fpsprobesize",
            str(self.options.get("fpsprobesize", 0)),
            "-f",
            self.options.get("input_driver", "v4l2"),
            "-thread_queue_size",
            str(self.options.get("thread_queue_size", 2)),
            "-video_size",
            f"{self.camera.width}x{self.camera.height}",
            "-framerate",
            str(self.camera.fps),
            # v4l2 hardware timestamps are often non-monotonic; wall clock is more
            # reliable for keeping DTS strictly increasing and avoiding frame bursts.
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            self.camera.device,
            # fps filter caps output to the configured rate inside ffmpeg so Python
            # never receives more frames than it will publish (avoids decoding waste).
            "-vf",
            f"scale={self.camera.width}:{self.camera.height}:flags=fast_bilinear,fps={output_fps},format=rgba",
            "-pix_fmt",
            "rgba",
            "-f",
            "rawvideo",
            "pipe:1",
        ]

        if input_format:
            cmd[cmd.index("-video_size") : cmd.index("-video_size")] = [
                "-input_format",
                input_format,
            ]

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=credential_minimized_environment(),
            start_new_session=True,
        )
