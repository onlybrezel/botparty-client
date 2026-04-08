"""Direct GStreamer publisher profile for low-latency LiveKit video."""

from __future__ import annotations

import asyncio
import shlex

from .base import BaseVideoProfile


class VideoProfile(BaseVideoProfile):
    profile_name = "gstreamer"

    def capture_mode(self) -> str:
        return "publisher"

    def publish_transport(self) -> str:
        return "livekit_direct"

    def _gstreamer_publisher_path(self) -> str:
        return str(
            self.options.get("publisher_path")
            or self.options.get("gstreamer_publisher_path")
            or "gstreamer-publisher"
        )

    def _build_pure_gstreamer_video_branch(self, target_bitrate_kbps: int | None) -> str:
        width = int(self.camera.width)
        height = int(self.camera.height)
        fps = max(1, int(round(self.output_fps())))
        bitrate = (
            int(target_bitrate_kbps)
            if isinstance(target_bitrate_kbps, int) and target_bitrate_kbps > 0
            else int(self.options.get("target_bitrate_kbps", 2500))
        )
        device = shlex.quote(str(self.camera.device))
        fourcc = (self.camera.fourcc or "").strip().upper()
        configured_input_format = str(self.options.get("input_format", "")).strip().lower()
        is_mjpeg = configured_input_format == "mjpeg" or fourcc == "MJPG"

        src = f"v4l2src device={device} do-timestamp=true"
        if is_mjpeg:
            src += (
                f" ! image/jpeg,width={width},height={height},framerate={fps}/1"
                " ! jpegdec"
            )
        else:
            src += f" ! video/x-raw,width={width},height={height},framerate={fps}/1"

        encoder = (
            "openh264enc "
            f"bitrate={bitrate * 1000} complexity=low gop-size={fps}"
        )
        if self.gst_element_exists("x264enc") and not self.gst_element_exists("openh264enc"):
            encoder = (
                "x264enc tune=zerolatency speed-preset=ultrafast "
                f"key-int-max={fps} bitrate={bitrate} bframes=0 threads=2"
            )

        return (
            f"{src} ! videoconvert ! queue ! {encoder} "
            "! h264parse config-interval=-1"
        )

    def _build_ffmpeg_video_command(self, target_bitrate_kbps: int | None) -> list[str]:
        width = int(self.camera.width)
        height = int(self.camera.height)
        fps = max(1, int(round(self.output_fps())))
        bitrate = (
            int(target_bitrate_kbps)
            if isinstance(target_bitrate_kbps, int) and target_bitrate_kbps > 0
            else int(self.options.get("target_bitrate_kbps", 2500))
        )
        codec = str(
            self.options.get("video_codec")
            or self.detect_default_h264_codec()
        ).strip()
        fourcc = (self.camera.fourcc or "").strip().upper()
        configured_input_format = str(self.options.get("input_format", "")).strip().lower()
        is_mjpeg = configured_input_format == "mjpeg" or fourcc == "MJPG"

        cmd = [
            str(self.options.get("ffmpeg_path", "ffmpeg")),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
        ]
        if is_mjpeg:
            cmd.extend(["-input_format", "mjpeg"])
        cmd.extend(
            [
                "-framerate",
                str(fps),
                "-video_size",
                f"{width}x{height}",
                "-i",
                str(self.camera.device),
                "-an",
                "-c:v",
                codec,
            ]
        )

        if codec == "libx264":
            cmd.extend(["-preset", "ultrafast", "-tune", "zerolatency"])
        else:
            cmd.extend(["-pix_fmt", "yuv420p"])

        cmd.extend(
            [
                "-b:v",
                f"{bitrate}k",
                "-maxrate",
                f"{bitrate}k",
                "-bufsize",
                f"{bitrate * 2}k",
                "-g",
                str(fps),
                "-bf",
                "0",
                "-f",
                "h264",
                "-",
            ]
        )
        return cmd

    def _build_video_upload_branch(self) -> str:
        return (
            "fdsrc fd=0 do-timestamp=true ! queue "
            "! video/x-h264,stream-format=byte-stream,alignment=au "
            "! h264parse config-interval=-1"
        )

    def _build_audio_branch(self) -> str | None:
        return None

    def _build_gstreamer_pipeline(self) -> str:
        branches = [self._build_video_upload_branch()]
        audio_branch = self._build_audio_branch()
        if audio_branch:
            branches.append(audio_branch)
        return " ".join(branches)

    def _use_ffmpeg_pipe(self) -> bool:
        preferred = str(
            self.options.get("publish_backend")
            or self.options.get("livekit_publish_backend")
            or "auto"
        ).strip().lower()
        ffmpeg_path = str(self.options.get("ffmpeg_path", "ffmpeg"))
        if preferred == "gstreamer":
            return False
        if preferred == "ffmpeg":
            return self.command_exists(ffmpeg_path)
        return self.command_exists(ffmpeg_path)

    async def spawn_livekit_process(
        self,
        *,
        livekit_url: str,
        token: str,
        target_bitrate_kbps: int | None,
    ):
        publisher_path = self._gstreamer_publisher_path()
        if not self.command_exists(publisher_path):
            raise RuntimeError(
                "gstreamer-publisher is not installed. Install it first or switch back to ffmpeg/legacy mode."
            )

        pipeline = self._build_gstreamer_pipeline()
        publisher_cmd = [
            publisher_path,
            "--url",
            livekit_url,
            "--token",
            token,
            "--",
            *shlex.split(pipeline),
        ]

        if self._use_ffmpeg_pipe():
            ffmpeg_cmd = self._build_ffmpeg_video_command(target_bitrate_kbps)
            shell_cmd = (
                f"{shlex.join(ffmpeg_cmd)} | "
                f"{shlex.join(publisher_cmd)}"
            )
            return await asyncio.create_subprocess_shell(
                shell_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=self.gstreamer_env(),
                executable="/bin/bash",
            )

        pure_gst_pipeline = self._build_pure_gstreamer_video_branch(target_bitrate_kbps)
        fallback_cmd = [
            publisher_path,
            "--url",
            livekit_url,
            "--token",
            token,
            "--",
            *shlex.split(pure_gst_pipeline),
        ]
        return await asyncio.create_subprocess_exec(
            *fallback_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=self.gstreamer_env(),
        )
