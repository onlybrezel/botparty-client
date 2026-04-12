"""BotParty video profile registry."""

from __future__ import annotations

import importlib
from typing import Final

from ..config import RobotConfig
from .base import BaseVideoProfile

PROFILE_ALIASES: Final[dict[str, str]] = {
    "opencv": "opencv",
    "ffmpeg": "ffmpeg",
    "ffmpeg-arecord": "ffmpeg_arecord",
    "ffmpeg_arecord": "ffmpeg_arecord",
    "ffmpeg-hud": "ffmpeg_hud",
    "ffmpeg_hud": "ffmpeg_hud",
    "ffmpeg-libcamera": "ffmpeg_libcamera",
    "ffmpeg_libcamera": "ffmpeg_libcamera",
    "gstreamer": "gstreamer",
    "gstreamer-livekit": "gstreamer",
    "gstreamer_livekit": "gstreamer",
    "gstreamer_arecord": "gstreamer_arecord",
    "gstreamer-arecord": "gstreamer_arecord",
    "gstreamer-arecord-livekit": "gstreamer_arecord",
    "gstreamer_arecord_livekit": "gstreamer_arecord",
    "botparty_streamer": "botparty_streamer",
    "botparty-streamer": "botparty_streamer",
    "ffmpeg_tcp_livekit": "botparty_streamer",
    "lk_h264_publisher": "botparty_streamer",
    "lk-h264-publisher": "botparty_streamer",
    "go_h264_publisher": "botparty_streamer",
    "go-h264-publisher": "botparty_streamer",
    "cozmo_vid": "cozmo_vid",
    "vector_vid": "vector_vid",
    "none": "none",
}


def normalize_profile_name(name: str) -> str:
    key = name.strip().lower().replace("/", "_")
    return PROFILE_ALIASES.get(key, key.replace("-", "_"))


def create_video_profile(config: RobotConfig) -> BaseVideoProfile:
    profile = normalize_profile_name(config.video.type)
    module = importlib.import_module(f".{profile}", package=__name__)
    return module.VideoProfile(config)


__all__ = ["BaseVideoProfile", "create_video_profile", "normalize_profile_name"]
