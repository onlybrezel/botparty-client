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
