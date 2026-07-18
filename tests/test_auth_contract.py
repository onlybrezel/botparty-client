import asyncio
import json

import pytest

from botparty_robot.auth import AuthFailure, ClientAuthenticator
from botparty_robot.client_ops import ClientOpsMixin
from botparty_robot.config import RobotConfig, ServerConfig
from botparty_robot.protocol import MAX_CLAIM_RESPONSE_BYTES


class _Content:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def read(self, limit: int) -> bytes:
        return self._body[:limit]


class _Response:
    def __init__(self, status: int, body: bytes, content_length: int | None = None) -> None:
        self.status = status
        self.content = _Content(body)
        self.content_length = len(body) if content_length is None else content_length

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.posts = 0

    def post(self, *args, **kwargs) -> _Response:
        del args, kwargs
        self.posts += 1
        return self.response


class _AuthClient(ClientOpsMixin):
    def __init__(self, response: _Response) -> None:
        self.config = RobotConfig(
            server=ServerConfig(
                claim_token="claim-token-not-for-logs",  # secret-scan: allow-test-fixture
                device_key="d" * 64,
                api_url="https://botparty.live",
            )
        )
        self._session = _Session(response)
        self._camera_runtimes: list[object] = []
        self._capability_manifest: dict[str, object] = {}
        self._remote_target_bitrate_kbps = None
        self._configured_target_bitrate_kbps = None

    def _get_session(self) -> _Session:
        return self._session

    def _uses_direct_livekit_publisher(self) -> bool:
        return False

    def _effective_target_bitrate_kbps(self) -> int:
        return 1_200


def _valid_claim() -> bytes:
    return json.dumps(
        {
            "protocolVersion": 1,
            "token": "livekit-secret",
            "robotId": "robot-1",
            "livekitUrl": "wss://botparty.live",
            "robotAuthToken": "robot-auth-secret",
        }
    ).encode()


@pytest.mark.parametrize(
    ("status", "body", "content_length"),
    [
        (302, _valid_claim(), None),
        (401, _valid_claim(), None),
        (500, _valid_claim(), None),
        (200, b"<html>not json</html>", None),
        (200, b"{broken", None),
        (200, json.dumps({"robotId": "robot-1"}).encode(), None),
        (
            200,
            json.dumps(
                {
                    "token": " ",
                    "robotId": "robot-1",
                    "livekitUrl": "wss://botparty.live",
                    "robotAuthToken": " ",
                }
            ).encode(),
            None,
        ),
        (200, b"{}", MAX_CLAIM_RESPONSE_BYTES + 1),
        (200, b"x" * (MAX_CLAIM_RESPONSE_BYTES + 1), None),
    ],
)
def test_invalid_claim_never_creates_auth_state(
    status: int,
    body: bytes,
    content_length: int | None,
    caplog,
) -> None:
    client = _AuthClient(_Response(status, body, content_length))
    assert asyncio.run(client._authenticate()) is None
    assert client._session.posts == 1
    logs = caplog.text
    assert "claim-token-not-for-logs" not in logs
    assert "livekit-secret" not in logs
    assert "robot-auth-secret" not in logs


def test_valid_claim_creates_complete_typed_auth_state() -> None:
    client = _AuthClient(_Response(201, _valid_claim()))
    auth = asyncio.run(client._authenticate())
    assert auth is not None
    assert auth.robot_id == "robot-1"
    assert auth.token == "livekit-secret"
    assert auth.robot_auth_token == "robot-auth-secret"
    assert auth.livekit_url == "wss://botparty.live"


def test_claim_transport_failure_is_a_typed_redacted_outcome() -> None:
    class BrokenSession:
        def post(self, *args, **kwargs):
            del args, kwargs
            raise TimeoutError("sensitive transport context")

    config = RobotConfig(server=ServerConfig(claim_token="claim-token", device_key="d" * 64))
    result = asyncio.run(
        ClientAuthenticator(config, lambda: BrokenSession()).claim(
            publish_camera_ids=[], capabilities={"hardware": "none"}
        )
    )

    assert result == AuthFailure("transport_error", "TimeoutError")


def test_claim_rejects_insecure_livekit_url_and_accepts_capability_payload() -> None:
    response = _Response(
        200,
        json.dumps(
            {
                "token": "livekit-secret",
                "robotId": "robot-1",
                "livekitUrl": "ws://botparty.live",
                "robotAuthToken": "robot-auth-secret",
            }
        ).encode(),
    )
    session = _Session(response)
    config = RobotConfig(server=ServerConfig(claim_token="claim-token", device_key="d" * 64))
    result = asyncio.run(
        ClientAuthenticator(config, lambda: session).claim(
            publish_camera_ids=["front"], capabilities={"hardware": "none"}
        )
    )

    assert isinstance(result, AuthFailure)
    assert result.code == "unsafe_livekit_url"


def test_claim_rejects_missing_device_key_before_network_access() -> None:
    session = _Session(_Response(200, _valid_claim()))
    config = RobotConfig(server=ServerConfig(claim_token="claim-token"))

    result = asyncio.run(
        ClientAuthenticator(config, lambda: session).claim(publish_camera_ids=[], capabilities=None)
    )

    assert result == AuthFailure("protocol_rejected", "invalid claim request")
    assert session.posts == 0
