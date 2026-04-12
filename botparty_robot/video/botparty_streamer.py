"""Direct publisher profile using ffmpeg -> botparty-streamer -> LiveKit."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shlex
import zlib

from .base import BaseVideoProfile

logger = logging.getLogger("botparty.video.botparty_streamer")


class VideoProfile(BaseVideoProfile):
    profile_name = "botparty_streamer"

    def capture_mode(self) -> str:
        return "publisher"

    def publish_transport(self) -> str:
        return "livekit_direct"

    def _publisher_binary_path(self) -> str:
        return str(
            self.options.get("publisher_binary")
            or self.options.get("botparty_streamer_path")
            or self.options.get("lk_h264_publisher_path")
            or "botparty-streamer"
        )

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

        camera_id = self._camera_id()
        if camera_id == "front":
            return 5004

        base = int(self.options.get("publisher_tcp_port_base", 5600))
        slot = (zlib.crc32(camera_id.encode("utf-8")) % 200) + 1
        return base + slot

    def _decode_token_payload(self, token: str) -> dict[str, object]:
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
        width = int(self.camera.width)
        height = int(self.camera.height)
        input_fps = max(1, int(self.camera.fps))
        output_fps = max(1, int(round(self.output_fps())))
        bitrate = (
            int(target_bitrate_kbps)
            if isinstance(target_bitrate_kbps, int) and target_bitrate_kbps > 0
            else int(self.options.get("target_bitrate_kbps", 1200))
        )
        codec = str(self.options.get("video_codec") or self.detect_default_h264_codec()).strip()
        gop = max(output_fps, int(self.options.get("gop_frames", output_fps * 2)))

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
                str(self.camera.device),
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
                f"{bitrate * 2}k",
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

        env = os.environ.copy()
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
                "VIDEO_FPS": str(max(1, int(round(self.output_fps())))),
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
    ):
        publisher_path = self._publisher_binary_path()
        ffmpeg_path = str(self.options.get("ffmpeg_path", "ffmpeg"))

        if not self.command_exists(publisher_path):
            raise RuntimeError(
                f"Missing botparty-streamer binary ({publisher_path}). Install it on PATH or set video.options.publisher_binary"
            )
        if not self.command_exists(ffmpeg_path):
            raise RuntimeError("FFmpeg is missing. Install with: sudo apt install -y ffmpeg")

        codec = str(self.options.get("video_codec") or self.detect_default_h264_codec()).strip()
        if codec != "libx264" and not self.ffmpeg_supports("encoder", codec):
            raise RuntimeError(
                f"FFmpeg encoder '{codec}' is not available on this system. Choose a supported encoder or install codec packages."
            )

        port = self._tcp_port()
        ffmpeg_cmd = self._build_ffmpeg_command(port, target_bitrate_kbps)
        publisher_cmd = [publisher_path]
        env = self._build_publisher_env(livekit_url, token, port)

        shell_cmd = (
            "set -uo pipefail; "
            "cleanup() { "
            "status=$?; "
            '[[ -n "${publisher_pid:-}" ]] && kill "$publisher_pid" 2>/dev/null || true; '
            '[[ -n "${ffmpeg_pid:-}" ]] && kill "$ffmpeg_pid" 2>/dev/null || true; '
            '[[ -n "${publisher_pid:-}" ]] && wait "$publisher_pid" 2>/dev/null || true; '
            '[[ -n "${ffmpeg_pid:-}" ]] && wait "$ffmpeg_pid" 2>/dev/null || true; '
            'exit "$status"; '
            "}; "
            "trap cleanup EXIT INT TERM; "
            f"{shlex.join(publisher_cmd)} & "
            'publisher_pid=$!; '
            "sleep 0.4; "
            f"{shlex.join(ffmpeg_cmd)} & "
            'ffmpeg_pid=$!; '
            "while true; do "
            'if ! kill -0 "$publisher_pid" 2>/dev/null; then '
            'wait "$publisher_pid"; publisher_status=$?; '
            'echo "publisher exited with code ${publisher_status}" >&2; '
            'kill "$ffmpeg_pid" 2>/dev/null || true; '
            'wait "$ffmpeg_pid" 2>/dev/null || true; '
            'exit "$publisher_status"; '
            "fi; "
            'if ! kill -0 "$ffmpeg_pid" 2>/dev/null; then '
            'wait "$ffmpeg_pid"; ffmpeg_status=$?; '
            'echo "ffmpeg exited with code ${ffmpeg_status}" >&2; '
            'kill "$publisher_pid" 2>/dev/null || true; '
            'wait "$publisher_pid" 2>/dev/null || true; '
            'exit "$ffmpeg_status"; '
            "fi; "
            "sleep 0.2; "
            "done"
        )

        logger.info(
            "Starting botparty-streamer direct path: camera=%s track=%s tcp_port=%d codec=%s",
            self._camera_id(),
            self._track_name(),
            port,
            codec,
        )

        return await asyncio.create_subprocess_shell(
            shell_cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            executable="/bin/bash",
        )
