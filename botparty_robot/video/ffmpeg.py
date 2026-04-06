"""FFmpeg-backed video profile."""

from __future__ import annotations

import asyncio

from .base import BaseVideoProfile

FFMPEG_INPUT_FORMAT_MAP = {
    "MJPG": "mjpeg",
    "YUYV": "yuyv422",
}


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg"

    def capture_mode(self) -> str:
        return "ffmpeg"

    async def spawn_ffmpeg_process(self):
        fourcc = (self.camera.fourcc or "mjpeg").strip().upper()
        input_format = FFMPEG_INPUT_FORMAT_MAP.get(fourcc, fourcc.lower())
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
            "-input_format",
            input_format,
            "-video_size",
            f"{self.camera.width}x{self.camera.height}",
            "-framerate",
            str(self.camera.fps),
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            self.camera.device,
            "-pix_fmt",
            "rgba",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

