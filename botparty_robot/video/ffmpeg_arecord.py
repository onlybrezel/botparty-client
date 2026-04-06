"""FFmpeg video plus arecord microphone capture."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ..audio import list_alsa_devices, resolve_alsa_device
from .ffmpeg import VideoProfile as FFmpegVideoProfile

logger = logging.getLogger("botparty.camera")


class VideoProfile(FFmpegVideoProfile):
    profile_name = "ffmpeg_arecord"

    def has_audio(self) -> bool:
        return True

    async def start_audio(self, rtc, room, running):
        sample_rate = int(self.options.get("audio_sample_rate", 48000))
        channels = int(self.options.get("audio_channels", 1))
        chunk_ms = int(self.options.get("audio_chunk_ms", 10))
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

        # Keep order stable while removing duplicates.
        candidate_devices = list(dict.fromkeys(candidate_devices))

        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        if room is not None:
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
                "Starting arecord capture: device=%s sample_rate=%d channels=%d",
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
