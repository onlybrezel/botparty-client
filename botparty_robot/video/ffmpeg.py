"""FFmpeg-backed video profile."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .base import BaseVideoProfile

FFMPEG_INPUT_FORMAT_MAP = {
    "MJPG": "mjpeg",
    "YUYV": "yuyv422",
}

logger = logging.getLogger("botparty.video.ffmpeg")
ACTIVE_STREAMER_VERSION_URL = "https://stats.botparty.live/get_active_version.php?app=streamer"


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg"

    def __init__(self, config) -> None:
        super().__init__(config)
        self._direct_profile: BaseVideoProfile | None = None
        self._streamer_binary_path: str | None = None
        self._installed_streamer_version: str | None = None
        self._streamer_expected_version = self._resolve_streamer_expected_version()
        self._maybe_enable_direct_publisher()

    def _fetch_active_streamer_version(self) -> str | None:
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "--max-time", "6", ACTIVE_STREAMER_VERSION_URL],
                capture_output=True,
                text=True,
                check=False,
                timeout=8,
            )
        except Exception:
            return None

        if result.returncode != 0:
            return None

        raw = (result.stdout or "").strip().splitlines()
        if not raw:
            return None
        return self.normalize_streamer_version(raw[0].strip())

    def _resolve_streamer_expected_version(self) -> str:
        active = self._fetch_active_streamer_version()
        if active:
            return active

        fallback = "v0.1.0"
        logger.warning(
            "Could not resolve active botparty-streamer version from stats endpoint, using fallback=%s",
            fallback,
        )
        return fallback

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

        for candidate in (
            "/usr/local/bin/botparty-streamer",
        ):
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

    def _maybe_install_streamer_binary(self) -> bool:
        install_script = Path(__file__).resolve().parents[2] / "scripts" / "install-botparty-streamer.sh"
        if not install_script.exists():
            logger.debug("botparty-streamer install script not found: %s", install_script)
            return False

        target_dir = str(self.managed_streamer_dir())

        cmd = [
            str(install_script),
            self._streamer_expected_version,
            "--dir",
            target_dir,
        ]

        logger.info(
            "Installing or updating botparty-streamer: version=%s dir=%s",
            self._streamer_expected_version,
            target_dir,
        )
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                timeout=90,
            )
        except Exception as exc:
            logger.warning("botparty-streamer install failed: %s", exc)
            return False

        if result.returncode != 0:
            tail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
            if tail:
                logger.warning("botparty-streamer install failed: %s", " | ".join(tail))
            else:
                logger.warning("botparty-streamer install failed with exit code %d", result.returncode)
            return False

        self._streamer_binary_path = f"{target_dir.rstrip('/')}/botparty-streamer"
        self.options["publisher_binary"] = self._streamer_binary_path
        self._installed_streamer_version = self.read_streamer_version_for_binary(self._streamer_binary_path)
        return True

    def _should_refresh_managed_streamer(self, current_binary: str | None) -> bool:
        managed_binary = self.managed_streamer_binary_path()
        managed_version = self.read_streamer_version_for_binary(managed_binary)
        managed_healthy = managed_binary.is_file() and os.access(managed_binary, os.X_OK) and self._probe_streamer_binary(str(managed_binary))

        if managed_healthy and managed_version == self._streamer_expected_version:
            self._installed_streamer_version = managed_version
            return False

        current_version = self.read_streamer_version_for_binary(current_binary)
        if current_version:
            self._installed_streamer_version = current_version

        if current_version and current_version != self._streamer_expected_version:
            logger.info(
                "A newer botparty-streamer is available for optimal performance: installed=%s active=%s. Updating now.",
                current_version,
                self._streamer_expected_version,
            )
        elif not current_version:
            logger.info(
                "botparty-streamer version is unknown or missing. Installing active version=%s before video startup.",
                self._streamer_expected_version,
            )

        return True

    def _maybe_enable_direct_publisher(self) -> None:
        streamer_binary = self._resolve_streamer_binary_path()
        healthy = bool(streamer_binary and self._probe_streamer_binary(streamer_binary))
        self._installed_streamer_version = self.read_streamer_version_for_binary(streamer_binary)
        install_attempted = False

        if self._should_refresh_managed_streamer(streamer_binary):
            install_attempted = True
            if self._maybe_install_streamer_binary():
                streamer_binary = self._resolve_streamer_binary_path()
                healthy = bool(streamer_binary and self._probe_streamer_binary(streamer_binary))
                self._installed_streamer_version = self.read_streamer_version_for_binary(streamer_binary)
                if self._installed_streamer_version:
                    logger.info("botparty-streamer is now up to date: %s", self._installed_streamer_version)
            else:
                logger.warning("botparty-streamer update/install failed; continuing with existing video path")

        if not healthy and not install_attempted:
            if not self._maybe_install_streamer_binary():
                logger.info("botparty-streamer unavailable; using legacy ffmpeg SDK transport")
                return
            streamer_binary = self._resolve_streamer_binary_path()
            healthy = bool(streamer_binary and self._probe_streamer_binary(streamer_binary))

        if not healthy or not streamer_binary:
            logger.info("botparty-streamer health check failed; using legacy ffmpeg SDK transport")
            return

        self._streamer_binary_path = streamer_binary
        self.options["publisher_binary"] = streamer_binary

        from .botparty_streamer import VideoProfile as BotPartyStreamerProfile

        self._direct_profile = BotPartyStreamerProfile(self.config)
        logger.info(
            "FFmpeg profile switched to botparty-streamer direct transport: binary=%s version=%s",
            streamer_binary,
            self._streamer_expected_version,
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
        output_fps = max(1, int(round(self.output_fps())))

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
            cmd[cmd.index("-video_size"):cmd.index("-video_size")] = ["-input_format", input_format]

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
