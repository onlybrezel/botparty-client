"""Simple holders for long-lived ffmpeg/arecord processes."""

from __future__ import annotations


class ProcessHandles:
    def __init__(self) -> None:
        self.video_process = None
        self.audio_process = None

