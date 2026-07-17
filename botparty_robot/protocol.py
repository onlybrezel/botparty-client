"""Versioned wire models for claim and privileged control messages."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 1
MAX_CLAIM_RESPONSE_BYTES = 128 * 1024
MAX_WEBSOCKET_MESSAGE_BYTES = 64 * 1024


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, hide_input_in_errors=True)


class StreamPolicy(WireModel):
    target_bitrate_kbps: int | None = Field(
        default=None,
        validation_alias=AliasChoices("targetBitrateKbps", "target_bitrate_kbps"),
        ge=150,
        le=3_000,
    )
    active_camera_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("activeCameraId", "active_camera_id"),
        max_length=32,
    )


class ClaimResponse(WireModel):
    protocol_version: int = Field(
        default=PROTOCOL_VERSION,
        validation_alias=AliasChoices("protocolVersion", "protocol_version"),
    )
    token: str = Field(min_length=1, max_length=16_384)
    robot_id: str = Field(
        validation_alias=AliasChoices("robotId", "robot_id"),
        min_length=1,
        max_length=128,
    )
    livekit_url: str = Field(
        validation_alias=AliasChoices("livekitUrl", "livekit_url"),
        min_length=1,
        max_length=2_048,
    )
    robot_auth_token: str = Field(
        validation_alias=AliasChoices("robotAuthToken", "robot_auth_token"),
        min_length=1,
        max_length=16_384,
    )
    publish_tokens: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("publishTokens", "publish_tokens"),
    )
    stream: StreamPolicy | None = None
    ingress: dict[str, Any] | None = None

    @field_validator("token", "robot_id", "livekit_url", "robot_auth_token")
    @classmethod
    def _nonblank_claim_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("claim value must not be blank")
        return normalized

    @field_validator("publish_tokens")
    @classmethod
    def _bounded_publish_tokens(cls, values: dict[str, str]) -> dict[str, str]:
        if len(values) > 8:
            raise ValueError("publishTokens may contain at most eight cameras")
        if any(
            not camera_id.strip() or len(camera_id) > 32 or not token.strip() or len(token) > 16_384
            for camera_id, token in values.items()
        ):
            raise ValueError("publishTokens contains an invalid camera id or token")
        return {camera_id.strip(): token.strip() for camera_id, token in values.items()}

    @field_validator("protocol_version")
    @classmethod
    def _supported_version(cls, value: int) -> int:
        if value != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {value}")
        return value


class ControlCommand(WireModel):
    command_id: str = Field(
        validation_alias=AliasChoices("commandId", "command_id", "actionId", "action_id"),
        min_length=1,
        max_length=128,
    )
    button_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("buttonId", "button_id"),
        max_length=128,
    )
    command: str = Field(min_length=1, max_length=64)
    value: Any = None
    timestamp: float
    metadata: dict[str, Any] | None = None
    user_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("userId", "user_id"),
        max_length=128,
    )
    client_timestamp: float | None = Field(
        default=None,
        validation_alias=AliasChoices("clientTimestamp", "client_timestamp"),
    )
    ack_required: bool = Field(
        default=False,
        validation_alias=AliasChoices("ackRequired", "ack_required"),
    )


class ControlAck(WireModel):
    command_id: str = Field(serialization_alias="commandId", min_length=1, max_length=128)
    status: Literal["ACK", "NACK"]
    message: str | None = Field(default=None, max_length=256)


class RemoteAction(WireModel):
    action_id: str = Field(
        validation_alias=AliasChoices("actionId", "action_id"),
        serialization_alias="actionId",
        min_length=1,
        max_length=128,
    )
    type: Literal[
        "restart_video",
        "restart_control",
        "reset_safety",
        "restart_tts",
        "restart_audio",
        "restart_chat",
        "update_client",
        "set_log_stream",
    ]
    duration_sec: int | None = Field(
        default=None,
        validation_alias=AliasChoices("durationSec", "duration_sec"),
        serialization_alias="durationSec",
        ge=10,
        le=900,
    )
    scopes: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("scopes")
    @classmethod
    def _normalize_scopes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(not value or len(value) > 64 for value in normalized):
            raise ValueError("action scopes must contain non-empty strings up to 64 characters")
        if len(set(normalized)) != len(normalized):
            raise ValueError("action scopes must be unique")
        return normalized

    @model_validator(mode="after")
    def _validate_fields_for_type(self) -> RemoteAction:
        if self.type != "set_log_stream" and self.duration_sec is not None:
            raise ValueError("durationSec is only valid for set_log_stream")
        return self


class ActionResult(WireModel):
    protocol_version: Literal[1] = Field(
        default=1,
        serialization_alias="protocolVersion",
    )
    action_id: str = Field(serialization_alias="actionId", max_length=128)
    state: Literal["accepted", "completed", "rejected", "failed"]
    code: str = Field(min_length=1, max_length=64)
    occurred_at_ms: int = Field(serialization_alias="occurredAtMs")
    detail: str | None = Field(default=None, max_length=240)
