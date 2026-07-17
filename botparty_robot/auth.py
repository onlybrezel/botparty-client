"""Strict robot claim service with redacted typed outcomes."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import aiohttp
from pydantic import ValidationError

from .config import RobotConfig, _validate_transport_url, normalize_livekit_url
from .protocol import MAX_CLAIM_RESPONSE_BYTES, ClaimResponse


@dataclass(frozen=True, slots=True)
class AuthResult:
    token: str
    robot_id: str
    livekit_url: str
    publish_tokens: dict[str, str]
    robot_auth_token: str
    target_bitrate_kbps: int | None


@dataclass(frozen=True, slots=True)
class AuthFailure:
    code: Literal[
        "transport_error",
        "redirect_rejected",
        "http_rejected",
        "response_too_large",
        "protocol_rejected",
        "unsafe_livekit_url",
    ]
    detail: str


class ClientAuthenticator:
    def __init__(
        self,
        config: RobotConfig,
        session: Callable[[], aiohttp.ClientSession],
    ) -> None:
        self._config = config
        self._session = session

    async def claim(
        self,
        *,
        publish_camera_ids: list[str],
        capabilities: dict[str, object] | None,
    ) -> AuthResult | AuthFailure:
        payload: dict[str, object] = {
            "claimToken": self._config.server.claim_token_value(),
            "deviceKey": self._config.server.device_key_value(),
            "publishCameraIds": publish_camera_ids,
        }
        if self._config.server.report_capabilities_in_claim and capabilities is not None:
            payload["capabilities"] = capabilities
        try:
            async with self._session().post(
                f"{self._config.server.api_url}/api/v1/robots/claim",
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status in (301, 302, 307, 308):
                    return AuthFailure("redirect_rejected", f"HTTP {response.status}")
                if response.status not in (200, 201):
                    return AuthFailure("http_rejected", f"HTTP {response.status}")
                if (
                    response.content_length is not None
                    and response.content_length > MAX_CLAIM_RESPONSE_BYTES
                ):
                    return AuthFailure("response_too_large", "claim response exceeds 128 KiB")
                raw = await response.content.read(MAX_CLAIM_RESPONSE_BYTES + 1)
        except Exception as exc:
            return AuthFailure("transport_error", type(exc).__name__)
        if len(raw) > MAX_CLAIM_RESPONSE_BYTES:
            return AuthFailure("response_too_large", "claim response exceeds 128 KiB")
        try:
            claim = ClaimResponse.model_validate(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            return AuthFailure("protocol_rejected", str(exc))
        livekit_url = normalize_livekit_url(claim.livekit_url)
        try:
            livekit_url = _validate_transport_url(
                livekit_url,
                secure_scheme="wss",
                insecure_scheme="ws",
                allow_insecure=self._config.server.allow_insecure_dev_transport,
                field_name="claim.livekitUrl",
            )
        except ValueError as exc:
            return AuthFailure("unsafe_livekit_url", str(exc))
        return AuthResult(
            token=claim.token,
            robot_id=claim.robot_id,
            livekit_url=livekit_url,
            publish_tokens=claim.publish_tokens,
            robot_auth_token=claim.robot_auth_token,
            target_bitrate_kbps=(
                claim.stream.target_bitrate_kbps if claim.stream is not None else None
            ),
        )
