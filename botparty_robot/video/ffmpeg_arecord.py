"""FFmpeg video plus arecord microphone capture."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..audio import AudioCapture, AudioCaptureConfig
from .ffmpeg import VideoProfile as FFmpegVideoProfile


class VideoProfile(FFmpegVideoProfile):
    profile_name = "ffmpeg_arecord"

    def has_audio(self) -> bool:
        return True

    async def start_audio(
        self,
        rtc: Any,
        room: Any,
        running: Callable[[], bool],
    ) -> None:
        capture = AudioCapture(AudioCaptureConfig.from_options(self.options))
        await capture.publish(rtc, room, running)
