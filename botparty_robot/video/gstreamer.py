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
            "-y",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-progress",
            "pipe:2",
            "-stats_period",
            str(self.options.get("stats_period_sec", 5)),
            "-avioflags",
            "direct",
            "-fflags",
            "nobuffer+discardcorrupt",
            "-flags",
            "low_delay",
            "-analyzeduration",
            str(self.options.get("analyzeduration", 0)),
            "-probesize",
            str(self.options.get("probesize", 32)),
            "-fpsprobesize",
            str(self.options.get("fpsprobesize", 0)),
            "-f",
            "v4l2",
            "-thread_queue_size",
            str(self.options.get("thread_queue_size", 2)),
        ]
        if is_mjpeg:
            cmd.extend(["-input_format", "mjpeg"])
        cmd.extend(
            [
                "-video_size",
                f"{width}x{height}",
                "-framerate",
                str(fps),
                "-use_wallclock_as_timestamps",
                "1",
                "-i",
                str(self.camera.device),
                "-an",
                "-fps_mode",
                "passthrough",
                "-vf",
                (
                    f"scale={width}:{height}:flags=fast_bilinear,"
                    f"fps={fps},setpts=N/({fps}*TB),format=yuv420p"
                ),
                "-c:v",
                codec,
            ]
        )

        if codec == "libx264":
            cmd.extend(["-preset", "ultrafast", "-tune", "zerolatency"])

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
                "-bsf:v",
                "dump_extra",
                "-f",
                "h264",
                "-",
            ]
        )
        return cmd

    def _build_video_upload_branch(self) -> str:
        return (
            "fdsrc fd=0 do-timestamp=true ! queue "
            "! h264parse config-interval=-1 disable-passthrough=true "
            "! video/x-h264,stream-format=byte-stream,alignment=au"
        )

    def _build_fifo_video_upload_branch(self, fifo_path: str) -> str:
        quoted_fifo = shlex.quote(fifo_path)
        return (
            f"filesrc location={quoted_fifo} ! queue "
            "! h264parse config-interval=-1 disable-passthrough=true"
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

    def _validate_gstreamer_dependencies(self, using_ffmpeg_pipe: bool) -> None:
        publisher_path = self._gstreamer_publisher_path()
        if not self.command_exists(publisher_path):
            raise RuntimeError(
                "Missing gstreamer-publisher binary. Install it with "
                "./scripts/install-gstreamer-publisher.sh"
            )

        if not self.command_exists("gst-inspect-1.0"):
            raise RuntimeError(
                "GStreamer runtime tools are missing (gst-inspect-1.0 not found). "
                "Install with: sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-base "
                "gstreamer1.0-plugins-good gstreamer1.0-plugins-bad"
            )

        required_elements = ["h264parse"]
        if using_ffmpeg_pipe:
            required_elements.extend(["filesrc", "queue"])
        else:
            required_elements.extend(["v4l2src", "videoconvert", "queue"])
            if not (self.gst_element_exists("openh264enc") or self.gst_element_exists("x264enc")):
                raise RuntimeError(
                    "No GStreamer H.264 encoder found (need openh264enc or x264enc). "
                    "Install with: sudo apt install -y gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly"
                )

        missing_elements = [name for name in required_elements if not self.gst_element_exists(name)]
        if missing_elements:
            joined = ", ".join(missing_elements)
            raise RuntimeError(
                f"Missing required GStreamer elements: {joined}. "
                "Install with: sudo apt install -y gstreamer1.0-tools gstreamer1.0-plugins-base "
                "gstreamer1.0-plugins-good gstreamer1.0-plugins-bad"
            )

        if using_ffmpeg_pipe:
            ffmpeg_path = str(self.options.get("ffmpeg_path", "ffmpeg"))
            if not self.command_exists(ffmpeg_path):
                raise RuntimeError(
                    "FFmpeg backend selected but ffmpeg was not found. "
                    "Install with: sudo apt install -y ffmpeg"
                )

            codec = str(self.options.get("video_codec") or self.detect_default_h264_codec()).strip()
            if codec != "libx264" and not self.ffmpeg_supports("encoder", codec):
                raise RuntimeError(
                    f"FFmpeg encoder '{codec}' is not available on this system. "
                    "Choose a supported encoder (for example libx264) or install the required codec package."
                )

    async def spawn_livekit_process(
        self,
        *,
        livekit_url: str,
        token: str,
        target_bitrate_kbps: int | None,
    ):
        publisher_path = self._gstreamer_publisher_path()
        use_ffmpeg_pipe = self._use_ffmpeg_pipe()
        self._validate_gstreamer_dependencies(use_ffmpeg_pipe)

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

        if use_ffmpeg_pipe:
            ffmpeg_cmd = self._build_ffmpeg_video_command(target_bitrate_kbps)
            fifo_option = self.options.get("video_fifo_path")
            camera_id = str(self.options.get("camera_id", "front")).strip() or "front"
            fifo_path = (
                str(fifo_option).strip()
                if fifo_option is not None and str(fifo_option).strip()
                else f"/tmp/botparty-gstreamer-video-{camera_id}.h264"
            )
            fifo_pipeline_parts = [self._build_fifo_video_upload_branch(fifo_path)]
            audio_branch = self._build_audio_branch()
            if audio_branch:
                fifo_pipeline_parts.append(audio_branch)
            fifo_pipeline = " ".join(fifo_pipeline_parts)
            fifo_publisher_cmd = [
                publisher_path,
                "--url",
                livekit_url,
                "--token",
                token,
                "--",
                *shlex.split(fifo_pipeline),
            ]
            ffmpeg_fifo_cmd = [*ffmpeg_cmd[:-1], fifo_path]
            shell_cmd = (
                "set -uo pipefail; "
                f"fifo={shlex.quote(fifo_path)}; "
                'rm -f "$fifo"; mkfifo "$fifo"; '
                'cleanup() { '
                'status=$?; '
                '[[ -n "${publisher_pid:-}" ]] && kill "$publisher_pid" 2>/dev/null || true; '
                '[[ -n "${ffmpeg_pid:-}" ]] && kill -9 "$ffmpeg_pid" 2>/dev/null || true; '
                '[[ -n "${ffmpeg_pid:-}" ]] && wait "$ffmpeg_pid" 2>/dev/null || true; '
                'rm -f "$fifo"; '
                'exit "$status"; '
                '}; '
                "trap cleanup EXIT INT TERM; "
                f"{shlex.join(fifo_publisher_cmd)} & "
                'publisher_pid=$!; '
                "sleep 1; "
                f"{shlex.join(ffmpeg_fifo_cmd)} & "
                'ffmpeg_pid=$!; '
                'while true; do '
                'if ! kill -0 "$publisher_pid" 2>/dev/null; then '
                'wait "$publisher_pid"; publisher_status=$?; '
                'echo "publisher exited with code ${publisher_status}" >&2; '
                'kill "$ffmpeg_pid" 2>/dev/null || true; '
                'wait "$ffmpeg_pid" 2>/dev/null || true; '
                'exit "$publisher_status"; '
                'fi; '
                'if ! kill -0 "$ffmpeg_pid" 2>/dev/null; then '
                'wait "$ffmpeg_pid"; ffmpeg_status=$?; '
                'echo "ffmpeg exited with code ${ffmpeg_status}" >&2; '
                'kill "$publisher_pid" 2>/dev/null || true; '
                'wait "$publisher_pid" 2>/dev/null || true; '
                'exit "$ffmpeg_status"; '
                'fi; '
                'sleep 0.2; '
                'done'
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
