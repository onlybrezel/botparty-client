"""Base classes for BotParty video profiles."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from ..artifacts import VerifiedExecutable, open_verified_streamer, verify_installed_streamer
from ..config import RobotConfig
from ..process_group import ManagedProcessGroup, run_sandboxed

MediaProcess = asyncio.subprocess.Process | ManagedProcessGroup


class BaseVideoProfile:
    profile_name = "base"
    _ffmpeg_feature_cache: ClassVar[dict[tuple[str, str, str], bool]] = {}
    _streamer_version_pattern = re.compile(r"v?\d+\.\d+\.\d+(?:[-.][A-Za-z0-9._]+)?")

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.camera = config.camera
        self.options = config.video.options

    def capture_mode(self) -> str:
        return "opencv"

    def publish_transport(self) -> str:
        return "livekit"

    def frame_dimensions(self) -> tuple[int, int, float]:
        return self.camera.width, self.camera.height, float(self.camera.fps)

    def output_fps(self) -> float:
        explicit = self.options.get("publish_fps")
        if isinstance(explicit, (int, float)) and explicit > 0:
            return min(float(self.camera.fps), float(explicit))

        recommended = self._recommended_publish_fps()
        if recommended is None:
            return float(self.camera.fps)
        return min(float(self.camera.fps), recommended)

    def _recommended_publish_fps(self) -> float | None:
        model = self._read_platform_model()
        if not model:
            return None

        pixels = self.camera.width * self.camera.height
        model_lower = model.lower()

        if "raspberry pi 3" in model_lower:
            if pixels >= 1280 * 720:
                return 10.0
            if pixels >= 960 * 540:
                return 12.0
            return 15.0

        if "raspberry pi 4" in model_lower:
            if pixels >= 1280 * 720:
                return 25.0
            if pixels >= 960 * 540:
                return 25.0
            return 30.0

        if "raspberry pi zero" in model_lower or "raspberry pi 2" in model_lower:
            if pixels >= 960 * 540:
                return 8.0
            return 12.0

        return None

    def _read_platform_model(self) -> str | None:
        for path in (
            "/sys/firmware/devicetree/base/model",
            "/proc/device-tree/model",
        ):
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    value = handle.read().replace("\x00", "").strip()
                if value:
                    return value
            except Exception:
                continue
        return None

    def transform_rgba(self, frame_rgba: Any, frame_width: int, frame_height: int) -> Any:
        return frame_rgba

    async def spawn_ffmpeg_process(self) -> MediaProcess:
        raise NotImplementedError

    async def spawn_livekit_process(
        self,
        *,
        livekit_url: str,
        token: str,
        target_bitrate_kbps: int | None,
    ) -> MediaProcess:
        """Start a native direct publisher when supported by the profile."""

        raise NotImplementedError

    async def capture_sdk_frames(
        self,
        rtc: Any,
        source: Any,
        running: Callable[[], bool],
        on_frame: Callable[[], None],
    ) -> None:
        raise NotImplementedError

    async def run_disabled(self, running: Callable[[], bool]) -> None:
        while running():
            await asyncio.sleep(30)

    def has_audio(self) -> bool:
        return False

    def botparty_streamer_version(self) -> str | None:
        return None

    def repo_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    def managed_streamer_dir(self) -> Path:
        configured = os.getenv("BOTPARTY_STREAMER_DIR", "").strip()
        if configured:
            return Path(configured).expanduser()
        state_dir = os.getenv("BOTPARTY_STATE_DIR", "").strip()
        if state_dir:
            return Path(state_dir).expanduser() / "bin"
        return self.repo_root() / ".botparty" / "bin"

    def managed_streamer_binary_path(self) -> Path:
        return self.managed_streamer_dir() / "botparty-streamer"

    def managed_streamer_version_file(self) -> Path:
        return self.managed_streamer_dir() / "botparty-streamer.version"

    def verify_streamer_binary(self, binary_path: str | os.PathLike[str]) -> str:
        expected = self.options.get("publisher_sha256")
        return verify_installed_streamer(
            Path(binary_path),
            str(expected) if isinstance(expected, str) and expected.strip() else None,
        )

    def open_streamer_binary(self, binary_path: str | os.PathLike[str]) -> VerifiedExecutable:
        expected = self.options.get("publisher_sha256")
        return open_verified_streamer(
            Path(binary_path),
            str(expected) if isinstance(expected, str) and expected.strip() else None,
        )

    def read_streamer_version_for_binary(
        self, binary_path: str | os.PathLike[str] | None
    ) -> str | None:
        if not binary_path:
            return None

        path = Path(binary_path)

        # Version metadata is read from the trusted installation sidecar. Runtime
        # discovery must never execute a native binary merely to identify it.
        candidate_files = (
            path.parent / f"{path.name}.version",
            path.parent / "botparty-streamer.version",
        )
        for candidate in candidate_files:
            try:
                raw = candidate.read_text(encoding="utf-8").strip()
            except Exception:
                raw = ""
            normalized = self.normalize_streamer_version(raw)
            if normalized:
                return normalized

        # Last resort: version embedded in the file name, for example
        # botparty-streamer-v0.1.0-linux-arm64.
        match = self._streamer_version_pattern.search(path.name)
        if match:
            return self.normalize_streamer_version(match.group(0))
        return None

    def normalize_streamer_version(self, raw: str | None) -> str | None:
        if not raw:
            return None
        value = raw.strip()
        if not value:
            return None
        if value.startswith("v"):
            return value
        if self._streamer_version_pattern.fullmatch(value):
            return f"v{value}"
        return None

    async def start_audio(self, rtc: Any, room: Any, running: Callable[[], bool]) -> None:
        return

    def ffmpeg_supports(self, kind: str, name: str) -> bool:
        ffmpeg_path = str(self.options.get("ffmpeg_path", "ffmpeg"))
        cache_key = (ffmpeg_path, kind, name)
        cached = self._ffmpeg_feature_cache.get(cache_key)
        if cached is not None:
            return cached

        flag = {
            "encoder": "-encoders",
            "muxer": "-muxers",
        }.get(kind)
        if flag is None:
            return False

        try:
            result = run_sandboxed(
                [ffmpeg_path, "-hide_banner", flag],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            supported = result.returncode == 0 and name in result.stdout
        except Exception:
            supported = False

        self._ffmpeg_feature_cache[cache_key] = supported
        return supported

    def command_exists(self, name: str) -> bool:
        return shutil.which(name) is not None

    def detect_default_h264_codec(self) -> str:
        explicit = self.options.get("video_codec")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()

        model = (self._read_platform_model() or "").lower()
        if "raspberry pi" in model:
            for candidate in ("h264_v4l2m2m", "h264_omx", "libx264"):
                if candidate == "libx264" or self.ffmpeg_supports("encoder", candidate):
                    return candidate
        return "libx264"
