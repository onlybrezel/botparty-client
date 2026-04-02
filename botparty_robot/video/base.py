"""Base classes for BotParty video profiles."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from ..config import RobotConfig


class BaseVideoProfile:
    profile_name = "base"

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.camera = config.camera
        self.options = config.video.options

    def capture_mode(self) -> str:
        return "opencv"

    def frame_dimensions(self) -> tuple[int, int, float]:
        return self.camera.width, self.camera.height, float(self.camera.fps)

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

