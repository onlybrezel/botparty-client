"""Vector camera profile."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..hardware.vector import get_vector_robot
from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "vector_vid"

    def capture_mode(self) -> str:
        return "sdk"

    async def capture_sdk_frames(
        self,
        rtc: Any,
        source: Any,
        running: Callable[[], bool],
        on_frame: Callable[[], None],
    ) -> None:
        while running():
            robot = get_vector_robot()
            if robot is None:
                await asyncio.sleep(0.25)
                continue
            image = getattr(robot.camera, "latest_image", None)
            if image is None:
                await asyncio.sleep(0.04)
                continue
            pil_image = image.annotate_image() if self.options.get("annotated") else image.raw_image
            frame_image = pil_image.convert("RGBA").resize((self.camera.width, self.camera.height))
            frame = rtc.VideoFrame(
                self.camera.width,
                self.camera.height,
                rtc.VideoBufferType.RGBA,
                frame_image.tobytes(),
            )
            source.capture_frame(frame)
            on_frame()
            await asyncio.sleep(1.0 / max(self.camera.fps, 1))
