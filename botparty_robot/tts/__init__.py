"""BotParty TTS profile registry."""

from __future__ import annotations

import importlib
from typing import Final

from ..config import RobotConfig
from .base import BaseTTSProfile

PROFILE_ALIASES: Final[dict[str, str]] = {
    "none": "none",
    "espeak": "espeak",
    "espeak-loop": "espeak_loop",
    "espeak_loop": "espeak_loop",
    "festival": "festival",
    "google_cloud": "google_cloud",
    "pico": "pico",
    "polly": "polly",
    "cozmo_tts": "cozmo_tts",
    "vector_tts": "vector_tts",
    "custom": "custom",
}


def normalize_profile_name(name: str) -> str:
    key = name.strip().lower().replace("/", "_")
    return PROFILE_ALIASES.get(key, key.replace("-", "_"))


def create_tts_profile(config: RobotConfig) -> BaseTTSProfile:
    profile = normalize_profile_name(config.tts.type)
    module = importlib.import_module(f".{profile}", package=__name__)
    adapter = module.TTSProfile(config)
    adapter.setup()
    return adapter


__all__ = ["BaseTTSProfile", "create_tts_profile", "normalize_profile_name"]
