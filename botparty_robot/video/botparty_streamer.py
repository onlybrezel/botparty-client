"""Direct publisher profile using ffmpeg -> botparty-streamer -> LiveKit."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import shlex
import zlib

from ..audio import list_alsa_devices, resolve_alsa_device
from .base import BaseVideoProfile

logger = logging.getLogger("botparty.video.botparty_streamer")


class VideoProfile(BaseVideoProfile):
    profile_name = "botparty_streamer"

    def has_audio(self) -> bool:
        return True

    async def start_audio(self, rtc, room, running):
        if room is None:
            logger.warning("Direct audio requested without LiveKit room; skipping audio publish")
            return

        sample_rate = int(self.options.get("audio_sample_rate", 48000))
        channels = int(self.options.get("audio_channels", 1))
        chunk_ms = int(self.options.get("audio_chunk_ms", 40))
        queue_frames = max(1, int(self.options.get("audio_queue_frames", 8)))
        samples_per_channel = sample_rate * chunk_ms // 1000
        bytes_per_sample = 2
        frame_bytes = samples_per_channel * channels * bytes_per_sample
        arecord_path = self.options.get("arecord_path", "arecord")
        requested_audio_device = str(self.options.get("audio_device", "default"))
        audio_device = resolve_alsa_device(requested_audio_device, "capture")
        audio_format = self.options.get("arecord_format", "S16_LE")

        candidate_devices: list[str] = [audio_device]
        requested_normalized = requested_audio_device.strip().lower()
        if requested_normalized in {"", "default", "pulse"}:
            for dev in list_alsa_devices("capture"):
                candidate_devices.append(f"plughw:{dev['hw']}")

        candidate_devices = list(dict.fromkeys(candidate_devices))

        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await room.local_participant.publish_track(track, publish_options)

        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=queue_frames)
        dropped_chunks = 0

        async def _read_stdout(proc_stdout) -> None:
            nonlocal dropped_chunks
            try:
                while running():
                    chunk = await proc_stdout.readexactly(frame_bytes)
                    if queue.full():
                        with contextlib.suppress(asyncio.QueueEmpty):
                            queue.get_nowait()
                        dropped_chunks += 1
                        if dropped_chunks % 200 == 0:
                            logger.warning(
                                "Audio capture backlog detected; dropped_chunks=%d queue_frames=%d",
                                dropped_chunks,
                                queue_frames,
                            )
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(chunk)
            except asyncio.IncompleteReadError:
                return
            finally:
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait(None)

        async def _publish_audio() -> None:
            while running():
                chunk = await queue.get()
                if chunk is None:
                    return
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=sample_rate,
                    num_channels=channels,
                    samples_per_channel=samples_per_channel,
                )
                await source.capture_frame(frame)

        async def _drain_stderr(proc_stderr) -> None:
            while True:
                line = await proc_stderr.readline()
                if not line:
                    return
                msg = line.decode("utf-8", errors="replace").strip()
                if msg:
                    logger.warning("arecord: %s", msg)

        last_error: str | None = None
        for idx, current_audio_device in enumerate(candidate_devices):
            if not running():
                return

            logger.info(
                "Starting arecord capture for botparty-streamer: device=%s sample_rate=%d channels=%d",
                current_audio_device,
                sample_rate,
                channels,
            )
            proc = await asyncio.create_subprocess_exec(
                arecord_path,
                "-q",
                "-D",
                str(current_audio_device),
                "-f",
                str(audio_format),
                "-c",
                str(channels),
                "-r",
                str(sample_rate),
                "-t",
                "raw",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stderr_task = None
            read_task = None
            publish_task = None
            try:
                if proc.stdout is None:
                    return

                read_task = asyncio.create_task(_read_stdout(proc.stdout))
                publish_task = asyncio.create_task(_publish_audio())
                if proc.stderr is not None:
                    stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

                await asyncio.gather(read_task, publish_task)
            finally:
                for task in (read_task, publish_task, stderr_task):
                    if task is not None:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await task
                await asyncio.shield(self._shutdown_audio_process(proc))

            if proc.returncode in (None, 0):
                return

            last_error = f"arecord exited with code {proc.returncode} on device {current_audio_device}"
            if idx + 1 < len(candidate_devices):
                logger.warning("%s; trying fallback capture device", last_error)
                continue

        if last_error:
            logger.error("Audio capture stopped: %s", last_error)

    async def _shutdown_audio_process(self, proc) -> None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()

        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass

        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(proc.wait(), timeout=2)

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
            return str(explicit)

        managed = self.managed_streamer_binary_path()
        if managed.is_file() and os.access(managed, os.X_OK):
            return str(managed)

        for candidate in ("/usr/local/bin/botparty-streamer",):
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return "botparty-streamer"

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
        # Use the lower 10 bits of the CRC32 hash to distribute across 1024 slots.
        # CRC32 has good uniformity for short strings; with up to ~16 cameras the
        # collision probability is negligible (birthday bound ~2%).  If an exact
        # port conflicts, set video.options.publisher_tcp_port explicitly.
        slot = (zlib.crc32(camera_id.encode("utf-8")) & 0x3FF) + 1
        return base + slot

    def _decode_token_payload(self, token: str) -> dict[str, object]:
        # Note: this decodes the JWT payload WITHOUT signature verification.
        # The token is not used for authentication here — it is verified by the
        # LiveKit server when the publisher connects. We only extract the
        # identity/room fields so the Go streamer can be configured correctly.
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
                f"Missing botparty-streamer binary ({publisher_path}). Install it with ./scripts/install-botparty-streamer.sh or set video.options.publisher_binary"
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
