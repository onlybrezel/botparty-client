"""libcamera-vid + ffmpeg profile for modern Raspberry Pi camera stacks."""

from __future__ import annotations

import asyncio
import os

from ..process_group import (
    ManagedProcessGroup,
    credential_minimized_environment,
    terminate_async_process,
)
from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg_libcamera"

    def capture_mode(self) -> str:
        return "ffmpeg"

    async def spawn_ffmpeg_process(self) -> ManagedProcessGroup:
        libcam = str(self.options.get("libcamera_path", "libcamera-vid"))
        ffmpeg = str(self.options.get("ffmpeg_path", "ffmpeg"))
        output_fps = round(self.camera.fps)
        libcamera_command = [
            libcam,
            "-t",
            "0",
            "--width",
            str(self.camera.width),
            "--height",
            str(self.camera.height),
            "--framerate",
            str(self.camera.fps),
            "--codec",
            "yuv420",
            "--nopreview",
            "-o",
            "-",
        ]
        ffmpeg_command = [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-avioflags",
            "direct",
            "-fflags",
            "nobuffer",
            "-flags",
            "low_delay",
            "-analyzeduration",
            "0",
            "-probesize",
            "32",
            "-fpsprobesize",
            "0",
            "-f",
            "rawvideo",
            "-pixel_format",
            "yuv420p",
            "-video_size",
            f"{self.camera.width}x{self.camera.height}",
            "-framerate",
            str(self.camera.fps),
            "-use_wallclock_as_timestamps",
            "1",
            "-i",
            "-",
            "-vf",
            f"scale={self.camera.width}:{self.camera.height}:flags=fast_bilinear,"
            f"fps={output_fps},format=rgba",
            "-pix_fmt",
            "rgba",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        read_fd, write_fd = os.pipe()
        environment = credential_minimized_environment()
        producer = None
        try:
            producer = await asyncio.create_subprocess_exec(
                *libcamera_command,
                stdout=write_fd,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
            consumer = await asyncio.create_subprocess_exec(
                *ffmpeg_command,
                stdin=read_fd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=environment,
                start_new_session=True,
            )
        except Exception:
            if producer is not None and producer.returncode is None:
                await terminate_async_process(producer)
            raise
        finally:
            os.close(read_fd)
            os.close(write_fd)
        return ManagedProcessGroup((producer, consumer), stdout=consumer.stdout)
