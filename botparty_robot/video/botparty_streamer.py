"""Direct publisher profile using ffmpeg -> botparty-streamer -> LiveKit."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import socket
import zlib
from collections.abc import Callable
from typing import Any

from ..audio import AudioCapture, AudioCaptureConfig
from ..process_group import (
    ManagedProcessGroup,
    credential_minimized_environment,
    terminate_async_process,
)
from .base import BaseVideoProfile

logger = logging.getLogger("botparty.video.botparty_streamer")


class VideoProfile(BaseVideoProfile):
    profile_name = "botparty_streamer"

    def has_audio(self) -> bool:
        return True

    async def start_audio(
        self,
        rtc: Any,
        room: Any,
        running: Callable[[], bool],
    ) -> None:
        if room is None:
            logger.warning("Direct audio requested without LiveKit room; skipping audio publish")
            return
        capture = AudioCapture(AudioCaptureConfig.from_options(self.options))
        await capture.publish(rtc, room, running)

    def capture_mode(self) -> str:
        return "publisher"

    def publish_transport(self) -> str:
        return "livekit_direct"

    def botparty_streamer_version(self) -> str | None:
        return self.read_streamer_version_for_binary(self._publisher_binary_path())

    def _publisher_binary_path(self) -> str:
        explicit = (
            self.options.get("publisher_binary")
            or self.options.get("botparty_streamer_path")
            or self.options.get("lk_h264_publisher_path")
        )
        if explicit:
            path = os.path.abspath(os.path.expanduser(str(explicit)))
            return path

        managed = self.managed_streamer_binary_path()
        if managed.is_file() and os.access(managed, os.X_OK):
            return str(managed)

        return str(managed)

    def _camera_id(self) -> str:
        camera_id = str(self.options.get("camera_id", "front")).strip()
        return camera_id or "front"

    def _track_name(self) -> str:
        explicit = str(self.options.get("track_name", "")).strip()
        if explicit:
            return explicit
        camera_id = self._camera_id()
        return "camera" if camera_id == "front" else f"camera.{camera_id}"

    def _tcp_port(self) -> int:
        explicit = self.options.get("publisher_tcp_port")
        if isinstance(explicit, int) and 1024 <= explicit <= 65535:
            return explicit

        base = int(self.options.get("publisher_tcp_port_base", 5600))
        camera_id = self._camera_id()
        if camera_id == "front":
            if self._is_local_port_available(5004):
                return 5004
            logger.warning("Default publisher port 5004 is busy; selecting fallback port")

        window = 1024
        slot = zlib.crc32(camera_id.encode("utf-8")) % window
        for offset in range(window):
            candidate = base + ((slot + offset) % window) + 1
            if self._is_local_port_available(candidate):
                if offset > 0:
                    logger.warning(
                        "Publisher port collision detected for %s, using fallback port %d",
                        camera_id,
                        candidate,
                    )
                return candidate

        raise RuntimeError(f"No free publisher TCP port in range [{base + 1}, {base + window}]")

    def _is_local_port_available(self, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            with contextlib.suppress(OSError):
                sock.close()

    def _decode_token_payload(self, token: str) -> dict[str, object]:
        # LiveKit verifies the token when the publisher connects. This local
        # decode only extracts identity and room fields for streamer configuration.
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        payload = parts[1]
        padded = payload + "=" * (-len(payload) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
            decoded = json.loads(raw.decode("utf-8"))
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}

    def _extract_identity_room(self, token: str) -> tuple[str, str]:
        payload = self._decode_token_payload(token)
        identity = str(
            payload.get("sub")
            or payload.get("identity")
            or self.options.get("livekit_identity")
            or f"robot-{self._camera_id()}"
        ).strip()

        room = ""
        video_claim = payload.get("video")
        if isinstance(video_claim, dict):
            room = str(video_claim.get("room") or "").strip()
        if not room:
            room = str(self.options.get("livekit_room") or "robot-room").strip()

        return identity or f"robot-{self._camera_id()}", room

    def _build_ffmpeg_command(self, port: int, target_bitrate_kbps: int | None) -> list[str]:
        device = str(self.camera.device)
        if "\x00" in device or "\n" in device or "\r" in device:
            raise ValueError(f"Camera device path contains invalid characters: {device!r}")
        width = int(self.camera.width)
        height = int(self.camera.height)
        input_fps = max(1, int(self.camera.fps))
        output_fps = max(1, round(self.output_fps()))
        bitrate = (
            int(target_bitrate_kbps)
            if isinstance(target_bitrate_kbps, int) and target_bitrate_kbps > 0
            else int(self.options.get("target_bitrate_kbps", 1200))
        )
        codec = str(self.options.get("video_codec") or self.detect_default_h264_codec()).strip()
        configured_gop = self.options.get("gop_frames")
        gop = max(output_fps, int(configured_gop if configured_gop is not None else output_fps * 2))

        cmd = [
            str(self.options.get("ffmpeg_path", "ffmpeg")),
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            str(self.options.get("loglevel", "warning")),
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
            str(self.options.get("input_driver", "v4l2")),
            "-thread_queue_size",
            str(self.options.get("thread_queue_size", 2)),
        ]

        input_format = str(self.options.get("input_format", "")).strip().lower()
        fourcc = (self.camera.fourcc or "").strip().upper()
        if input_format:
            cmd.extend(["-input_format", input_format])
        elif fourcc == "MJPG":
            cmd.extend(["-input_format", "mjpeg"])

        cmd.extend(
            [
                "-video_size",
                f"{width}x{height}",
                "-framerate",
                str(input_fps),
                "-use_wallclock_as_timestamps",
                "1",
                "-i",
                device,
                "-an",
                "-fps_mode",
                "passthrough",
                "-vf",
                f"scale={width}:{height}:flags=fast_bilinear,fps={output_fps},setpts=N/({output_fps}*TB),format=yuv420p",
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
                f"{bitrate}k",
                "-g",
                str(gop),
                "-keyint_min",
                str(gop),
                "-bf",
                "0",
                "-f",
                "h264",
                f"tcp://127.0.0.1:{port}?tcp_nodelay=1",
            ]
        )
        return cmd

    def _build_publisher_env(self, livekit_url: str, token: str, port: int) -> dict[str, str]:
        identity, room = self._extract_identity_room(token)
        track_name = self._track_name()

        env = credential_minimized_environment()
        env.update(
            {
                "LK_URL": livekit_url,
                "LK_TOKEN": token,
                "LK_ROOM": room,
                "LK_IDENTITY": identity,
                "LK_NAME": identity,
                "LK_TRACK_NAME": track_name,
                "INPUT_LISTEN_ADDR": f"127.0.0.1:{port}",
                "ALLOW_REMOTE_INPUT": "false",
                "VIDEO_FPS": str(max(1, round(self.output_fps()))),
                "FRAME_CHAN_SIZE": str(int(self.options.get("frame_chan_size", 4))),
                "MAX_PUBLISH_STALE_MS": str(int(self.options.get("max_publish_stale_ms", 250))),
                "AU_MAX_NALUS": str(int(self.options.get("au_max_nalus", 64))),
                "AU_MAX_BYTES": str(int(self.options.get("au_max_bytes", 2097152))),
                "INPUT_READ_TIMEOUT_MS": str(int(self.options.get("input_read_timeout_ms", 500))),
                "FRAME_FLUSH_TIMEOUT_MS": str(int(self.options.get("frame_flush_timeout_ms", 50))),
                "RECONNECT_MIN_MS": str(int(self.options.get("reconnect_min_ms", 250))),
                "RECONNECT_MAX_MS": str(int(self.options.get("reconnect_max_ms", 4000))),
            }
        )
        return env

    async def spawn_livekit_process(
        self,
        *,
        livekit_url: str,
        token: str,
        target_bitrate_kbps: int | None,
    ) -> ManagedProcessGroup:
        publisher_path = self._publisher_binary_path()
        ffmpeg_path = str(self.options.get("ffmpeg_path", "ffmpeg"))

        if not self.command_exists(publisher_path):
            raise RuntimeError(
                f"Missing botparty-streamer binary ({publisher_path}). Install it with "
                "./scripts/install-botparty-streamer.sh or set "
                "video.options.publisher_binary"
            )
        if not self.command_exists(ffmpeg_path):
            raise RuntimeError("FFmpeg is missing. Install with: sudo apt install -y ffmpeg")

        codec = str(self.options.get("video_codec") or self.detect_default_h264_codec()).strip()
        if codec != "libx264" and not self.ffmpeg_supports("encoder", codec):
            raise RuntimeError(
                f"FFmpeg encoder '{codec}' is unavailable. Choose a supported encoder or "
                "install the required codec package."
            )

        port = self._tcp_port()
        ffmpeg_cmd = self._build_ffmpeg_command(port, target_bitrate_kbps)
        publisher_env = self._build_publisher_env(livekit_url, token, port)
        ffmpeg_env = credential_minimized_environment()

        logger.info(
            "Starting botparty-streamer direct path: camera=%s track=%s tcp_port=%d codec=%s",
            self._camera_id(),
            self._track_name(),
            port,
            codec,
        )

        with self.open_streamer_binary(publisher_path) as verified_publisher:
            publisher = await asyncio.create_subprocess_exec(
                verified_publisher.exec_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=publisher_env,
                start_new_session=True,
                pass_fds=verified_publisher.pass_fds,
            )
        await asyncio.sleep(0.4)
        if publisher.returncode is not None:
            raise RuntimeError(
                f"botparty-streamer exited during startup with code {publisher.returncode}"
            )
        try:
            ffmpeg = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=ffmpeg_env,
                start_new_session=True,
            )
        except Exception:
            await terminate_async_process(publisher)
            raise
        return ManagedProcessGroup((publisher, ffmpeg))
