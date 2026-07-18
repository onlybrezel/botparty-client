"""HUD overlay profile inspired by RemoTV's ffmpeg-hud."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg_hud"

    def capture_mode(self) -> str:
        return "opencv"

    def transform_rgba(self, frame_rgba: Any, frame_width: int, frame_height: int) -> Any:
        try:
            import numpy as np
            from PIL import Image, ImageDraw
        except ImportError:
            return frame_rgba

        image = Image.fromarray(frame_rgba, mode="RGBA")
        draw = ImageDraw.Draw(image)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draw.rectangle((12, 12, 340, 84), fill=(0, 0, 0, 150))
        draw.text((24, 20), f"BotParty HUD  {now}", fill=(255, 255, 255, 255))
        draw.text(
            (24, 44),
            f"{self.config.hardware.type}  {frame_width}x{frame_height}",
            fill=(150, 220, 255, 255),
        )
        return np.array(image)
