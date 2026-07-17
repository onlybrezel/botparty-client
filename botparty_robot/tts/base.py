"""Base classes for BotParty TTS profiles."""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from typing import Any

from ..audio import resolve_alsa_device, set_alsa_volume
from ..config import RobotConfig
from .common import terminate_active_tts_processes

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
        self.max_characters = config.tts.max_characters
        self.rate_limit_count = config.tts.rate_limit_count
        self.rate_limit_window_sec = config.tts.rate_limit_window_sec
        self.daily_character_budget = config.tts.daily_character_budget
        self.operation_timeout_sec = config.tts.operation_timeout_sec
        self._recent_messages: dict[str, deque[float]] = defaultdict(deque)
        self._budget_day = time.strftime("%Y-%m-%d", time.gmtime())
        self._daily_characters = 0
        set_alsa_volume(self.playback_device, self.volume)

    def setup(self) -> None:
        """Optional setup hook."""

    def can_handle(self) -> bool:
        return self.enabled

    def should_speak(self, message: str, metadata: dict[str, Any] | None = None) -> bool:
        if not self.can_handle() or not message.strip():
            return False

        if len(message) > self.max_characters:
            logger.info("Skipping TTS message longer than %d characters", self.max_characters)
            return False

        metadata = metadata or {}
        sender = metadata.get("sender")
        if isinstance(sender, str) and sender.strip().lower() in self.blocked_senders:
            logger.info("Skipping TTS from blocked sender: %s", sender)
            return False

        is_anonymous = bool(metadata.get("anonymous")) or metadata.get("type") == "anon"
        if not sender:
            is_anonymous = True
        if is_anonymous and not self.allow_anonymous:
            logger.info("Skipping anonymous TTS message")
            return False

        if self.filter_urls and URL_RE.search(message):
            logger.info("Skipping TTS message because it contains a URL")
            return False

        if self.profile_name in {"polly", "google_cloud"} and not bool(
            self.options.get("cloud_data_processing_accepted", False)
        ):
            logger.warning("Cloud TTS is disabled until cloud_data_processing_accepted is true")
            return False

        now = time.monotonic()
        identity = (
            sender.strip().lower() if isinstance(sender, str) and sender.strip() else "anonymous"
        )
        recent = self._recent_messages[identity]
        while recent and now - recent[0] > self.rate_limit_window_sec:
            recent.popleft()
        if len(recent) >= self.rate_limit_count:
            logger.info("Skipping TTS message because the sender rate limit was reached")
            return False

        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._budget_day:
            self._budget_day = today
            self._daily_characters = 0
        if (
            self.daily_character_budget == 0
            or self._daily_characters + len(message) > self.daily_character_budget
        ):
            logger.warning("Skipping TTS message because the daily character budget was reached")
            return False

        recent.append(now)
        self._daily_characters += len(message)

        return True

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def mute(self) -> None:
        self.enabled = False
        terminate_active_tts_processes()

    def unmute(self) -> None:
        self.enabled = True

    def set_volume(self, level: int) -> None:
        self.volume = max(0, min(level, 100))
        set_alsa_volume(self.playback_device, self.volume)
