"""Looped eSpeak playback profile."""

from __future__ import annotations

from .espeak import TTSProfile as EspeakProfile


class TTSProfile(EspeakProfile):
    profile_name = "espeak_loop"

