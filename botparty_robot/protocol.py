"""Versioned wire models for claim and privileged control messages."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = 1
MAX_CLAIM_RESPONSE_BYTES = 128 * 1024
MAX_WEBSOCKET_MESSAGE_BYTES = 64 * 1024
MAX_JSON_DEPTH = 6
MAX_JSON_NODES = 512
MAX_JSON_KEYS = 64
MAX_JSON_LIST_ITEMS = 128
MAX_JSON_STRING_LENGTH = 4096
STANDARD_MOTION_COMMANDS = {"forward", "backward", "left", "right"}
TTS_TEXT_COMMANDS = {"chat", "say", "speak", "tts", "tts:say", "tts.say"}


def validate_bounded_json(value: Any) -> Any:
    """Reject values that are not bounded, finite JSON data."""

    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ValueError("JSON value contains too many nodes")
        if depth > MAX_JSON_DEPTH:
            raise ValueError("JSON value exceeds maximum nesting depth")
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            if len(item) > MAX_JSON_STRING_LENGTH:
                raise ValueError("JSON string exceeds 4096 characters")
            return
        if isinstance(item, int):
            if abs(item) > 2**53:
                raise ValueError("JSON integer exceeds the interoperable range")
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("JSON number must be finite")
            return
        if isinstance(item, list):
            if len(item) > MAX_JSON_LIST_ITEMS:
                raise ValueError("JSON list contains too many items")
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            if len(item) > MAX_JSON_KEYS:
                raise ValueError("JSON object contains too many keys")
            for key, child in item.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise ValueError("JSON object key is invalid")
                visit(child, depth + 1)
            return
        raise ValueError("value is not JSON-compatible")

    visit(value, 0)
    return value


def validate_command_value(
    command: str,
    value: Any,
    *,
    is_motion: bool = False,
    hardware_command: bool = False,
) -> Any:
    """Apply closed common command-value schemas before profile dispatch."""

    validate_bounded_json(value)
    normalized = command.strip().lower().replace("-", "_")
    if is_motion or normalized in STANDARD_MOTION_COMMANDS:
        if value is None:
            return value
        if isinstance(value, bool):
            raise ValueError("motion value must not be boolean")
        if isinstance(value, (int, float)) and -100 <= float(value) <= 100:
            return value
        if isinstance(value, dict) and set(value) == {"x", "y"}:
            axes = (value["x"], value["y"])
            if all(
                isinstance(axis, (int, float))
                and not isinstance(axis, bool)
                and -1 <= float(axis) <= 1
                for axis in axes
            ):
                return value
        raise ValueError("motion value must be null, -100..100 or normalized x/y")
    if normalized in TTS_TEXT_COMMANDS:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            allowed = {"text", "message", "value", "sender", "userId", "anonymous", "type"}
            if not set(value) <= allowed:
                raise ValueError("speech value contains unknown fields")
            text = next((value.get(key) for key in ("text", "message", "value")), None)
            if text is not None and not isinstance(text, str):
                raise ValueError("speech text must be a string")
            if "anonymous" in value and not isinstance(value["anonymous"], bool):
                raise ValueError("speech anonymous flag must be boolean")
            if any(
                key in value and not isinstance(value[key], str)
                for key in ("sender", "userId", "type")
            ):
                raise ValueError("speech identity fields must be strings")
            return value
        raise ValueError("speech value must be a string or closed speech object")
    if normalized in {"tts:volume", "tts.volume"}:
        raw = value
        if isinstance(value, dict) and set(value) <= {"level", "volume"}:
            raw = value.get("level", value.get("volume"))
        if isinstance(raw, bool):
            raise ValueError("speech volume must be numeric")
        try:
            volume = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("speech volume must be numeric") from exc
        if not 0 <= volume <= 100:
            raise ValueError("speech volume must be between 0 and 100")
    elif hardware_command and value is not None:
        raise ValueError("non-motion hardware commands do not accept a value")
    return value


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


class ClaimRequest(WireModel):
    claim_token: str = Field(serialization_alias="claimToken", min_length=1, max_length=16_384)
    device_key: str = Field(
        serialization_alias="deviceKey",
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    publish_camera_ids: list[str] = Field(
        default_factory=list, serialization_alias="publishCameraIds", max_length=8
    )
    capabilities: dict[str, Any] | None = None

    @field_validator("claim_token", "device_key")
    @classmethod
    def _nonblank_request_secret(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("claim request credential must not be blank")
        return normalized

    @field_validator("publish_camera_ids")
    @classmethod
    def _bounded_camera_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 32 for value in normalized):
            raise ValueError("publish camera id is invalid")
        if len(set(normalized)) != len(normalized):
            raise ValueError("publish camera ids must be unique")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def _bounded_capabilities(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_bounded_json(value) if value is not None else None


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

    @field_validator("ingress")
    @classmethod
    def _bounded_ingress(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return validate_bounded_json(value) if value is not None else None

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

    @field_validator("value", "metadata")
    @classmethod
    def _bounded_command_json(cls, value: Any) -> Any:
        return validate_bounded_json(value)


class ControlAck(WireModel):
    command_id: str = Field(serialization_alias="commandId", min_length=1, max_length=128)
    status: Literal["ACK", "NACK"]
    state: Literal[
        "accepted",
        "completed",
        "rejected",
        "failed",
        "superseded",
        "cancelled_by_stop",
    ]
    message: str | None = Field(default=None, max_length=256)


class OutcomeDeliveryAck(WireModel):
    outcome_id: str = Field(
        validation_alias=AliasChoices("outcomeId", "outcome_id"),
        min_length=64,
        max_length=64,
        pattern=r"^[a-f0-9]{64}$",
    )


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


class RemoteActionsPayload(WireModel):
    stream: StreamPolicy | None = None
    actions: list[RemoteAction] = Field(default_factory=list, max_length=32)


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
