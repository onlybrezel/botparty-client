"""Base classes for BotParty video profiles."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..config import RobotConfig


class BaseVideoProfile:
    profile_name = "base"
    _ffmpeg_feature_cache: dict[tuple[str, str, str], bool] = {}
    _gstreamer_feature_cache: dict[tuple[str, str], bool] = {}

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
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    value = handle.read().replace("\x00", "").strip()
                if value:
                    return value
            except Exception:
                continue
        return None

    def transform_rgba(self, frame_rgba: Any, frame_width: int, frame_height: int):
        return frame_rgba

    async def spawn_ffmpeg_process(self):
        raise NotImplementedError

    async def capture_sdk_frames(self, rtc, source, running: Callable[[], bool], on_frame: Callable[[], None]) -> None:
        raise NotImplementedError

    async def run_disabled(self, running: Callable[[], bool]) -> None:
        while running():
            await asyncio.sleep(30)

    def has_audio(self) -> bool:
        return False

    async def start_audio(self, rtc, room, running: Callable[[], bool]) -> None:
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
            result = subprocess.run(
                [ffmpeg_path, "-hide_banner", flag],
                capture_output=True,
                text=True,
                check=False,
            )
            supported = result.returncode == 0 and name in result.stdout
        except Exception:
            supported = False

        self._ffmpeg_feature_cache[cache_key] = supported
        return supported

    def command_exists(self, name: str) -> bool:
        return shutil.which(name) is not None

    def gstreamer_env(self) -> dict[str, str]:
        env = os.environ.copy()
        configured = str(self.options.get("gst_plugin_path", "")).strip()
        paths: list[str] = []
        if configured:
            paths.extend(part for part in configured.split(":") if part)

        home = Path.home()
        defaults = [
            home / ".local/lib/gstreamer-1.0",
            Path("/usr/local/lib/gstreamer-1.0"),
            Path("/usr/local/lib/aarch64-linux-gnu/gstreamer-1.0"),
            Path("/usr/local/lib/arm-linux-gnueabihf/gstreamer-1.0"),
        ]
        for candidate in defaults:
            if candidate.exists():
                paths.append(str(candidate))

        existing = env.get("GST_PLUGIN_PATH", "").strip()
        if existing:
            paths.extend(part for part in existing.split(":") if part)

        if paths:
            deduped = list(dict.fromkeys(paths))
            env["GST_PLUGIN_PATH"] = ":".join(deduped)
        return env

    def gst_element_exists(self, name: str) -> bool:
        gst_inspect = shutil.which("gst-inspect-1.0")
        if not gst_inspect:
            return False

        plugin_path = self.gstreamer_env().get("GST_PLUGIN_PATH", "")
        cache_key = (f"{gst_inspect}:{plugin_path}", name)
        cached = self._gstreamer_feature_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            result = subprocess.run(
                [gst_inspect, name],
                env=self.gstreamer_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            supported = result.returncode == 0
        except Exception:
            supported = False

        self._gstreamer_feature_cache[cache_key] = supported
        return supported

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
