"""libcamera-vid + ffmpeg profile for modern Raspberry Pi camera stacks."""

from __future__ import annotations

import asyncio
import shlex

from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg_libcamera"

    def capture_mode(self) -> str:
        return "ffmpeg"

    async def spawn_ffmpeg_process(self):
        libcam = shlex.quote(str(self.options.get("libcamera_path", "libcamera-vid")))
        ffmpeg = shlex.quote(str(self.options.get("ffmpeg_path", "ffmpeg")))
        cmd = (
            f"{libcam} -t 0 "
            f"--width {self.camera.width} --height {self.camera.height} "
            f"--framerate {self.camera.fps} --codec yuv420 --nopreview -o - | "
            f"{ffmpeg} -nostdin -hide_banner -loglevel error "
            f"-avioflags direct -fflags nobuffer -flags low_delay "
            f"-analyzeduration 0 -probesize 32 -fpsprobesize 0 "
            f"-f rawvideo -pixel_format yuv420p "
            f"-video_size {self.camera.width}x{self.camera.height} -framerate {self.camera.fps} "
            f"-use_wallclock_as_timestamps 1 -i - -pix_fmt rgba -f rawvideo pipe:1"
        )
        return await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

