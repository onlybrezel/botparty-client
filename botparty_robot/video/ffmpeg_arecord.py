"""FFmpeg video plus arecord microphone capture."""

from __future__ import annotations

import asyncio
import contextlib

from ..audio import resolve_alsa_device
from .ffmpeg import VideoProfile as FFmpegVideoProfile


class VideoProfile(FFmpegVideoProfile):
    profile_name = "ffmpeg_arecord"

    def has_audio(self) -> bool:
        return True

    async def start_audio(self, rtc, room, running):
        sample_rate = int(self.options.get("audio_sample_rate", 48000))
        channels = int(self.options.get("audio_channels", 1))
        chunk_ms = int(self.options.get("audio_chunk_ms", 10))
        samples_per_channel = sample_rate * chunk_ms // 1000
        bytes_per_sample = 2
        frame_bytes = samples_per_channel * channels * bytes_per_sample
        arecord_path = self.options.get("arecord_path", "arecord")
        audio_device = resolve_alsa_device(str(self.options.get("audio_device", "default")), "capture")
        audio_format = self.options.get("arecord_format", "S16_LE")

        source = rtc.AudioSource(sample_rate, channels)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", source)
        if room is not None:
            publish_options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track, publish_options)

        proc = await asyncio.create_subprocess_exec(
            arecord_path,
            "-q",
            "-D",
            str(audio_device),
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
        try:
            while running():
                if proc.stdout is None:
                    break
                chunk = await proc.stdout.readexactly(frame_bytes)
                frame = rtc.AudioFrame(
                    data=chunk,
                    sample_rate=sample_rate,
                    num_channels=channels,
                    samples_per_channel=samples_per_channel,
                )
                await source.capture_frame(frame)
        except asyncio.IncompleteReadError:
            return
        finally:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
