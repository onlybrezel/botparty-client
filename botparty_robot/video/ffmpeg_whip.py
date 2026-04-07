"""FFmpeg video profile that publishes directly to LiveKit WHIP ingress."""

from __future__ import annotations

import asyncio

from .base import BaseVideoProfile
from .ffmpeg import FFMPEG_INPUT_FORMAT_MAP


class VideoProfile(BaseVideoProfile):
    profile_name = "ffmpeg_whip"

    def capture_mode(self) -> str:
        return "whip"

    def publish_transport(self) -> str:
        return "whip"

    async def spawn_whip_process(self, publish_url: str, target_bitrate_kbps: int | None):
        if not self.ffmpeg_supports("muxer", "whip"):
            raise RuntimeError(
                "This ffmpeg build does not support the WHIP muxer. "
                "Install a newer ffmpeg build or switch back to the legacy LiveKit mode."
            )

        configured_input_format = str(self.options.get("input_format", "")).strip().lower()
        fourcc = (self.camera.fourcc or "").strip().upper()
        output_fps = max(1, int(round(self.output_fps())))
        video_codec = self.detect_default_whip_video_codec()
        target_bitrate = (
            int(target_bitrate_kbps)
            if isinstance(target_bitrate_kbps, int) and target_bitrate_kbps > 0
            else None
        )

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
            str(self.options.get("thread_queue_size", 4)),
            "-video_size",
            f"{self.camera.width}x{self.camera.height}",
            "-framerate",
            str(self.camera.fps),
        ]

        if input_format:
            cmd.extend(["-input_format", input_format])

        cmd.extend(
            [
                "-i",
                str(self.camera.device),
                "-an",
                "-vf",
                f"scale={self.camera.width}:{self.camera.height}:flags=fast_bilinear,"
                f"fps={output_fps},format=yuv420p",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                video_codec,
                "-g",
                str(output_fps),
                "-keyint_min",
                str(output_fps),
                "-bf",
                "0",
                "-f",
                "whip",
            ]
        )

        if video_codec == "libx264":
            cmd.extend(
                [
                    "-preset",
                    str(self.options.get("whip_preset", "veryfast")),
                    "-tune",
                    str(self.options.get("whip_tune", "zerolatency")),
                    "-profile:v",
                    str(self.options.get("whip_profile", "baseline")),
                    "-threads",
                    str(self.options.get("whip_threads", 1)),
                    "-sc_threshold",
                    "0",
                ]
            )

        if target_bitrate is not None:
            cmd.extend(
                [
                    "-b:v",
                    f"{target_bitrate}k",
                    "-maxrate",
                    f"{target_bitrate}k",
                    "-bufsize",
                    f"{max(target_bitrate * 2, 512)}k",
                ]
            )

        cmd.append(publish_url)

        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
