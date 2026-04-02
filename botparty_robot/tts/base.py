"""Base classes for BotParty TTS profiles."""

from __future__ import annotations

import logging
import re
from typing import Any

from ..audio import resolve_alsa_device, set_alsa_volume
from ..config import RobotConfig

logger = logging.getLogger("botparty.tts")
URL_RE = re.compile(r"(http|ftp|https)://[^\s]+", re.IGNORECASE)


class BaseTTSProfile:
    profile_name = "base"

    def __init__(self, config: RobotConfig) -> None:
        self.config = config
        self.options = config.tts.options
        self.enabled = config.tts.enabled
        self.playback_device = resolve_alsa_device(config.tts.playback_device, "playback")
        self.volume = config.tts.volume
        self.filter_urls = config.tts.filter_urls
        self.allow_anonymous = config.tts.allow_anonymous
        self.blocked_senders = {sender.strip().lower() for sender in config.tts.blocked_senders}
        self.delay_ms = config.tts.delay_ms
        set_alsa_volume(self.playback_device, self.volume)

    def setup(self) -> None:
        """Optional setup hook."""

    def can_handle(self) -> bool:
        return self.enabled

    def should_speak(self, message: str, metadata: dict[str, Any] | None = None) -> bool:
        if not self.can_handle() or not message.strip():
            return False

        metadata = metadata or {}
        sender = metadata.get("sender")
        if isinstance(sender, str) and sender.strip().lower() in self.blocked_senders:
            logger.info("Skipping TTS from blocked sender: %s", sender)
            return False

        is_anonymous = bool(metadata.get("anonymous")) or metadata.get("type") == "anon"
        if is_anonymous and not self.allow_anonymous:
            logger.info("Skipping anonymous TTS message")
            return False

        if self.filter_urls and URL_RE.search(message):
            logger.info("Skipping TTS message because it contains a URL")
            return False

        return True

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def mute(self) -> None:
        self.enabled = False

    def unmute(self) -> None:
        self.enabled = True

    def set_volume(self, level: int) -> None:
        self.volume = max(0, min(level, 100))
        set_alsa_volume(self.playback_device, self.volume)
