"""Looped eSpeak playback profile.

This class exists solely for config-name backwards compatibility.
Users who had ``tts.type: espeak_loop`` in their config continue to work
without needing to change anything.  There is no behavioural difference
from the regular ``espeak`` profile.
"""

from __future__ import annotations

from .espeak import TTSProfile as EspeakProfile


class TTSProfile(EspeakProfile):
    profile_name = "espeak_loop"

