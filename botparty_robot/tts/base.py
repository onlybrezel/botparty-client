"""Base classes for BotParty TTS profiles."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from ..audio import resolve_alsa_device, set_alsa_volume
from ..config import RobotConfig
from ..device_state import resolve_state_directory
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
        self._recent_messages: OrderedDict[str, deque[float]] = OrderedDict()
        self._sender_cleanup_counter = 0
        self._budget_day = time.strftime("%Y-%m-%d", time.gmtime())
        self._daily_characters = 0
        self._budget_state_path = resolve_state_directory(config.state) / "tts-budget.json"
        self._operation_generation = 0
        self._operation_lock = threading.Lock()
        self._worker_generation = threading.local()
        self.last_rejection_code: str | None = None
        set_alsa_volume(self.playback_device, self.volume)

    def setup(self) -> None:
        """Optional setup hook."""

    def can_handle(self) -> bool:
        return self.enabled

    def should_speak(self, message: str, metadata: dict[str, Any] | None = None) -> bool:
        self.last_rejection_code = None
        if not self.can_handle():
            self.last_rejection_code = "tts_disabled"
            return False
        if not message.strip():
            self.last_rejection_code = "tts_empty"
            return False

        if len(message) > self.max_characters:
            logger.info("Skipping TTS message longer than %d characters", self.max_characters)
            self.last_rejection_code = "tts_too_long"
            return False

        metadata = metadata or {}
        sender = metadata.get("sender")
        if isinstance(sender, str) and sender.strip().lower() in self.blocked_senders:
            logger.info("Skipping TTS from blocked sender: %s", sender)
            self.last_rejection_code = "tts_sender_blocked"
            return False

        is_anonymous = bool(metadata.get("anonymous")) or metadata.get("type") == "anon"
        if not sender:
            is_anonymous = True
        if is_anonymous and not self.allow_anonymous:
            logger.info("Skipping anonymous TTS message")
            self.last_rejection_code = "tts_anonymous_blocked"
            return False

        if self.filter_urls and URL_RE.search(message):
            logger.info("Skipping TTS message because it contains a URL")
            self.last_rejection_code = "tts_url_blocked"
            return False

        if self.profile_name in {"polly", "google_cloud"} and not bool(
            self.options.get("cloud_data_processing_accepted", False)
        ):
            logger.warning("Cloud TTS is disabled until cloud_data_processing_accepted is true")
            self.last_rejection_code = "tts_cloud_consent_required"
            return False

        now = time.monotonic()
        identity = (
            sender.strip().lower() if isinstance(sender, str) and sender.strip() else "anonymous"
        )
        if len(identity) > 128:
            identity = "sha256:" + hashlib.sha256(identity.encode()).hexdigest()
        self._sender_cleanup_counter += 1
        if self._sender_cleanup_counter >= 64 or len(self._recent_messages) >= 1024:
            self._sender_cleanup_counter = 0
            for existing_identity, timestamps in tuple(self._recent_messages.items()):
                while timestamps and now - timestamps[0] > self.rate_limit_window_sec:
                    timestamps.popleft()
                if not timestamps:
                    self._recent_messages.pop(existing_identity, None)
        recent = self._recent_messages.get(identity)
        if recent is None:
            if len(self._recent_messages) >= 1023:
                identity = "rate-limit-overflow"
                recent = self._recent_messages.get(identity)
            if recent is None:
                recent = deque()
                self._recent_messages[identity] = recent
        else:
            self._recent_messages.move_to_end(identity)
        while recent and now - recent[0] > self.rate_limit_window_sec:
            recent.popleft()
        if len(recent) >= self.rate_limit_count:
            logger.info("Skipping TTS message because the sender rate limit was reached")
            self.last_rejection_code = "tts_rate_limited"
            return False

        if not self._consume_daily_budget(len(message)):
            logger.warning("Skipping TTS message because the daily character budget was reached")
            self.last_rejection_code = "tts_budget_exhausted"
            return False

        recent.append(now)

        return True

    def _consume_daily_budget(self, character_count: int) -> bool:
        if self.daily_character_budget == 0:
            return False
        today = time.strftime("%Y-%m-%d", time.gmtime())
        state_dir = self._budget_state_path.parent
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = state_dir / ".tts-budget.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "r+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                stored_day = self._budget_day
                stored_count = self._daily_characters
                try:
                    raw = self._budget_state_path.read_text(encoding="utf-8")
                    if len(raw) > 1024:
                        raise ValueError("budget state is too large")
                    payload = json.loads(raw)
                    if isinstance(payload, dict):
                        stored_day = str(payload.get("day", stored_day))
                        stored_count = max(0, int(payload.get("characters", stored_count)))
                except FileNotFoundError:
                    pass
                except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
                    logger.warning("Ignoring invalid TTS budget state")

                if stored_day < today:
                    stored_day = today
                    stored_count = 0
                elif stored_day > today:
                    today = stored_day
                if stored_count + character_count > self.daily_character_budget:
                    self._budget_day = stored_day
                    self._daily_characters = stored_count
                    return False

                new_count = stored_count + character_count
                self._write_budget_state(self._budget_state_path, today, new_count)
                self._budget_day = today
                self._daily_characters = new_count
                return True
        except Exception:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            raise

    def _write_budget_state(self, path: Path, day: str, characters: int) -> None:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            mode="w",
            encoding="utf-8",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump({"day": day, "characters": characters}, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    def run_say(self, message: str, metadata: dict[str, Any] | None = None) -> None:
        with self._operation_lock:
            generation = self._operation_generation
        self._worker_generation.value = generation
        try:
            self.say(message, metadata)
        finally:
            self._worker_generation.value = None

    def operation_is_active(self) -> bool:
        generation = getattr(self._worker_generation, "value", None)
        with self._operation_lock:
            return (
                self.enabled and generation is not None and generation == self._operation_generation
            )

    def cancel_active(self) -> None:
        with self._operation_lock:
            self._operation_generation += 1
        terminate_active_tts_processes()

    def mute(self) -> None:
        self.enabled = False
        self.cancel_active()

    def unmute(self) -> None:
        self.enabled = True

    def set_volume(self, level: int) -> None:
        self.volume = max(0, min(level, 100))
        set_alsa_volume(self.playback_device, self.volume)
