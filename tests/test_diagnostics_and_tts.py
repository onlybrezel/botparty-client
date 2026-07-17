import asyncio
import json
import logging
import time
from collections import deque

import pytest

from botparty_robot.client_state import DiagnosticRecord, DiagnosticsBufferHandler, redact_text
from botparty_robot.config import DiagnosticsConfig, RobotConfig, ServerConfig, TTSConfig
from botparty_robot.diagnostics import DiagnosticsUploader
from botparty_robot.tts.base import BaseTTSProfile


class _TTS(BaseTTSProfile):
    profile_name = "test"

    def say(self, message: str, metadata: dict | None = None) -> None:
        del message, metadata


def _tts_config(**overrides: object) -> RobotConfig:
    values: dict[str, object] = {
        "enabled": True,
        "type": "espeak",
        "allow_anonymous": False,
        "max_characters": 20,
        "rate_limit_count": 2,
        "rate_limit_window_sec": 60,
        "daily_character_budget": 30,
    }
    values.update(overrides)
    return RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        tts=TTSConfig(**values),
    )


def test_diagnostics_sequences_are_stable_and_secrets_are_redacted() -> None:
    records: deque[DiagnosticRecord] = deque(maxlen=10)
    handler = DiagnosticsBufferHandler(records)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.diagnostics")

    handler.emit(
        logger.makeRecord(
            logger.name,
            logging.INFO,
            __file__,
            1,
            "Authorization: Bearer abc claim_token=def https://user:pass@example.com",
            (),
            None,
        )
    )
    handler.emit(logger.makeRecord(logger.name, logging.INFO, __file__, 2, "safe", (), None))

    assert [record.sequence for record in records] == [1, 2]
    assert "abc" not in records[0].line
    assert "def" not in records[0].line
    assert "user:pass" not in records[0].line
    assert "[REDACTED]" in records[0].line
    aws_test_key = "AKIA1234567890ABCDEF"  # secret-scan: allow-test-fixture
    assert aws_test_key not in redact_text(aws_test_key)


def test_tts_enforces_sender_content_rate_and_budget_limits(monkeypatch) -> None:
    monkeypatch.setattr("botparty_robot.tts.base.set_alsa_volume", lambda *_args: None)
    profile = _TTS(_tts_config())
    sender = {"sender": "viewer-1"}

    assert profile.should_speak("hello", sender) is True
    assert profile.should_speak("again", sender) is True
    assert profile.should_speak("third", sender) is False
    assert profile.should_speak("https://example.com", {"sender": "viewer-2"}) is False
    assert profile.should_speak("x" * 21, {"sender": "viewer-2"}) is False
    assert profile.should_speak("anonymous", None) is False


def test_cloud_tts_requires_explicit_data_processing_consent(monkeypatch) -> None:
    monkeypatch.setattr("botparty_robot.tts.base.set_alsa_volume", lambda *_args: None)
    profile = _TTS(_tts_config())
    profile.profile_name = "google_cloud"
    assert profile.should_speak("hello", {"sender": "viewer"}) is False


class _UploadResponse:
    content_type = "application/json"

    def __init__(self, status: int, ack_sequence: int | None = None) -> None:
        self.status = status
        self._ack_sequence = ack_sequence

    async def __aenter__(self) -> "_UploadResponse":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    @property
    def content(self) -> "_UploadResponse":
        return self

    async def read(self, limit: int) -> bytes:
        payload = {} if self._ack_sequence is None else {"ackSequence": self._ack_sequence}
        return json.dumps(payload).encode()[:limit]


class _UploadSession:
    def __init__(self, responses: list[_UploadResponse]) -> None:
        self._responses = deque(responses)
        self.payloads: list[dict[str, object]] = []

    def post(self, endpoint: str, *, json, headers, timeout) -> _UploadResponse:
        del endpoint, headers, timeout
        self.payloads.append(json)
        return self._responses.popleft()


def test_diagnostics_retry_partial_ack_and_deque_wrap_are_bounded() -> None:
    records: deque[DiagnosticRecord] = deque(maxlen=3)
    now = time.time()
    for sequence in range(1, 5):
        records.append(DiagnosticRecord(sequence, now, f"line-{sequence}"))
    assert [record.sequence for record in records] == [2, 3, 4]

    session = _UploadSession(
        [
            _UploadResponse(503),
            _UploadResponse(200, ack_sequence=3),
            _UploadResponse(204),
        ]
    )
    uploader = DiagnosticsUploader(
        config=DiagnosticsConfig(upload_enabled=True, batch_lines=3),
        api_url="https://botparty.live",
        records=records,
        session=lambda: session,  # type: ignore[arg-type]
        auth_token=lambda: "robot-token",
        client_id=lambda: "robot-1",
    )

    async def _scenario() -> None:
        assert await uploader.upload_once(now + 60) is False
        assert [record.sequence for record in records] == [2, 3, 4]
        assert await uploader.upload_once(now + 60) is True
        assert uploader.acked_sequence == 3
        assert [record.sequence for record in records] == [4]
        assert await uploader.upload_once(now + 60) is True
        assert list(records) == []

    asyncio.run(_scenario())
    assert session.payloads[0]["sequenceStart"] == 2
    assert session.payloads[0]["sequenceEnd"] == 4
    assert session.payloads[1] == session.payloads[0]


def test_diagnostics_rejects_ack_outside_the_sent_batch() -> None:
    now = time.time()
    records = deque([DiagnosticRecord(10, now, "line")], maxlen=3)
    session = _UploadSession([_UploadResponse(200, ack_sequence=99)])
    uploader = DiagnosticsUploader(
        config=DiagnosticsConfig(upload_enabled=True),
        api_url="https://botparty.live",
        records=records,
        session=lambda: session,  # type: ignore[arg-type]
        auth_token=lambda: "robot-token",
        client_id=lambda: "robot-1",
    )

    with pytest.raises(ValueError, match="outside the batch"):
        asyncio.run(uploader.upload_once(now + 60))
    assert [record.sequence for record in records] == [10]
