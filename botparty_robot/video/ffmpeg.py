"""FFmpeg-backed video profile."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess

from .base import BaseVideoProfile

FFMPEG_INPUT_FORMAT_MAP = {
    "MJPG": "mjpeg",
    "YUYV": "yuyv422",
}

logger = logging.getLogger("botparty.video.ffmpeg")


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg"

    def __init__(self, config) -> None:
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

        for candidate in ("/usr/local/bin/botparty-streamer",):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        on_path = shutil.which("botparty-streamer")
        return on_path or None

    def _probe_streamer_binary(self, binary_path: str) -> bool:
        try:
            result = subprocess.run(
                [binary_path],
                capture_output=True,
                text=True,
                check=False,
                timeout=4,
            )
        except Exception:
            return False

        combined = f"{result.stdout}\n{result.stderr}".lower()
        if "invalid config" in combined or "lk_url is required" in combined:
            return True
        return result.returncode == 0

    def _verify_streamer_binary(self, binary_path: str) -> bool:
        expected = str(self.options.get("publisher_binary_sha256") or "").strip().lower()
        if not expected:
            sidecar = self.managed_streamer_dir() / "botparty-streamer.sha256"
            if os.path.abspath(binary_path) == os.path.abspath(self.managed_streamer_binary_path()):
                try:
                    expected = sidecar.read_text(encoding="ascii").strip().lower()
                except OSError:
                    expected = ""
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            logger.warning("Ignoring unverified botparty-streamer binary: %s", binary_path)
            return False
        digest = hashlib.sha256()
        try:
            with open(binary_path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return False
        return digest.hexdigest() == expected

    def _maybe_enable_direct_publisher(self) -> None:
        streamer_binary = self._resolve_streamer_binary_path()
        verified = bool(streamer_binary and self._verify_streamer_binary(streamer_binary))
        healthy = bool(
            verified and streamer_binary and self._probe_streamer_binary(streamer_binary)
        )
        self._installed_streamer_version = self.read_streamer_version_for_binary(streamer_binary)

        if not healthy or not streamer_binary:
            logger.info("Verified botparty-streamer unavailable; using legacy ffmpeg SDK transport")
            return

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
    ):
        if self._direct_profile is None:
            raise RuntimeError("Direct publisher is not enabled for this ffmpeg profile")
        return await self._direct_profile.spawn_livekit_process(
            livekit_url=livekit_url,
            token=token,
            target_bitrate_kbps=target_bitrate_kbps,
        )

    async def spawn_ffmpeg_process(self):
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
        )
