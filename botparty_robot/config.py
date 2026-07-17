"""Validated configuration models for the robot client."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)

HARDWARE_PROFILES = frozenset(
    {
        "adafruit_pwm",
        "auto",
        "cozmo",
        "custom",
        "gopigo2",
        "gopigo3",
        "l298n",
        "maestro_servo",
        "max7219",
        "mc33926",
        "mdd10",
        "megapi_board",
        "motozero",
        "motor_hat",
        "mqtt_pub",
        "navq",
        "none",
        "owi_arm",
        "pololu",
        "serial_board",
        "telly",
        "thunderborg",
        "vector",
    }
)
VIDEO_PROFILES = frozenset(
    {
        "botparty_streamer",
        "cozmo_vid",
        "ffmpeg",
        "ffmpeg_arecord",
        "ffmpeg_hud",
        "ffmpeg_libcamera",
        "none",
        "opencv",
        "vector_vid",
    }
)
TTS_PROFILES = frozenset(
    {
        "cozmo_tts",
        "custom",
        "espeak",
        "espeak_loop",
        "festival",
        "google_cloud",
        "none",
        "pico",
        "polly",
        "vector_tts",
    }
)
CAMERA_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_default=True,
        hide_input_in_errors=True,
    )


def normalize_livekit_url(url: str) -> str:
    normalized = url.strip()
    if not normalized:
        return normalized

    parsed = urlsplit(normalized)
    path = parsed.path.rstrip("/")
    if path.endswith("/rtc"):
        path = path[:-4]
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def _is_loopback(hostname: str | None) -> bool:
    if not hostname:
        return False
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _validate_transport_url(
    value: str,
    *,
    secure_scheme: str,
    insecure_scheme: str,
    allow_insecure: bool,
    field_name: str,
) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if not parsed.hostname:
        raise ValueError(f"{field_name} must contain a hostname")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} must not contain user credentials")
    if parsed.fragment:
        raise ValueError(f"{field_name} must not contain a URL fragment")
    if parsed.scheme == secure_scheme:
        return normalized
    if parsed.scheme == insecure_scheme and allow_insecure and _is_loopback(parsed.hostname):
        return normalized
    raise ValueError(
        f"{field_name} must use {secure_scheme} (loopback {insecure_scheme} requires "
        "allow_insecure_dev_transport: true)"
    )


class ServerConfig(ConfigModel):
    api_url: str = "https://botparty.live"
    livekit_url: str = "wss://botparty.live"
    claim_token: SecretStr
    device_key: SecretStr | None = None
    robot_auth_token: SecretStr | None = None
    allow_insecure_dev_transport: bool = False
    report_capabilities_in_claim: bool = False

    @field_validator("livekit_url", mode="before")
    @classmethod
    def _normalize_livekit_url(cls, value: Any) -> Any:
        return normalize_livekit_url(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_urls(self) -> ServerConfig:
        self.api_url = _validate_transport_url(
            self.api_url,
            secure_scheme="https",
            insecure_scheme="http",
            allow_insecure=self.allow_insecure_dev_transport,
            field_name="server.api_url",
        )
        self.livekit_url = _validate_transport_url(
            self.livekit_url,
            secure_scheme="wss",
            insecure_scheme="ws",
            allow_insecure=self.allow_insecure_dev_transport,
            field_name="server.livekit_url",
        )
        if len(self.claim_token.get_secret_value().strip()) < 8:
            raise ValueError("server.claim_token must contain at least 8 characters")
        return self

    def claim_token_value(self) -> str:
        return self.claim_token.get_secret_value()

    def device_key_value(self) -> str | None:
        return self.device_key.get_secret_value() if self.device_key is not None else None

    def robot_auth_token_value(self) -> str | None:
        return (
            self.robot_auth_token.get_secret_value() if self.robot_auth_token is not None else None
        )


class CameraConfig(ConfigModel):
    width: int = Field(default=1280, ge=160, le=7680)
    height: int = Field(default=720, ge=120, le=4320)
    fps: int = Field(default=30, ge=1, le=120)
    device: str | int = "/dev/video0"
    backend: str = Field(default="v4l2", min_length=1, max_length=32)
    fourcc: str | None = Field(default="MJPG", min_length=4, max_length=4)
    buffer_size: int = Field(default=1, ge=1, le=8)
    warmup_frames: int = Field(default=4, ge=0, le=30)

    @field_validator("device")
    @classmethod
    def _device_must_be_valid(cls, value: str | int) -> str | int:
        if isinstance(value, int) and value < 0:
            raise ValueError("camera device index must be zero or greater")
        if isinstance(value, str) and not value.strip():
            raise ValueError("camera device path must not be empty")
        return value


class HardwareConfig(ConfigModel):
    type: str = "none"
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_profile(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace("/", "_")
        aliases = {"hardware_custom": "custom", "hardware_custom_example": "custom"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in HARDWARE_PROFILES:
            raise ValueError(
                f"unknown hardware profile {value!r}; choose one of: "
                f"{', '.join(sorted(HARDWARE_PROFILES))}"
            )
        return normalized


class VideoConfig(ConfigModel):
    type: str = "ffmpeg"
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_profile(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace("/", "_")
        aliases = {
            "ffmpeg_tcp_livekit": "botparty_streamer",
            "go_h264_publisher": "botparty_streamer",
            "lk_h264_publisher": "botparty_streamer",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in VIDEO_PROFILES:
            raise ValueError(
                f"unknown video profile {value!r}; choose one of: "
                f"{', '.join(sorted(VIDEO_PROFILES))}"
            )
        return normalized


class CameraVideoOverrideConfig(ConfigModel):
    type: str | None = None
    options: dict[str, Any] | None = None

    @field_validator("type")
    @classmethod
    def _known_profile(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return VideoConfig(type=value).type


class CameraStreamConfig(ConfigModel):
    id: str
    label: str | None = Field(default=None, max_length=80)
    role: Literal["primary", "secondary", "auxiliary"] | None = None
    enabled: bool = True
    publish_mode: Literal["always_on"] = "always_on"
    device: str | int | None = None
    width: int | None = Field(default=None, ge=160, le=7680)
    height: int | None = Field(default=None, ge=120, le=4320)
    fps: int | None = Field(default=None, ge=1, le=120)
    backend: str | None = Field(default=None, min_length=1, max_length=32)
    fourcc: str | None = Field(default=None, min_length=4, max_length=4)
    buffer_size: int | None = Field(default=None, ge=1, le=8)
    warmup_frames: int | None = Field(default=None, ge=0, le=30)
    video: CameraVideoOverrideConfig | None = None

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not CAMERA_ID_RE.fullmatch(normalized):
            raise ValueError(
                "camera id must start with a letter and contain only lowercase letters, "
                "digits, '_' or '-' (maximum 32 characters)"
            )
        return normalized


class TTSConfig(ConfigModel):
    enabled: bool = False
    type: str = "none"
    playback_device: str = Field(default="default", min_length=1, max_length=128)
    volume: int = Field(default=70, ge=0, le=100)
    chat_to_tts: bool = True
    filter_urls: bool = True
    allow_anonymous: bool = False
    blocked_senders: list[str] = Field(default_factory=list)
    delay_ms: int = Field(default=0, ge=0, le=30_000)
    max_characters: int = Field(default=300, ge=1, le=2_000)
    rate_limit_count: int = Field(default=5, ge=1, le=100)
    rate_limit_window_sec: int = Field(default=60, ge=1, le=3_600)
    daily_character_budget: int = Field(default=20_000, ge=0, le=10_000_000)
    operation_timeout_sec: int = Field(default=20, ge=1, le=120)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _known_profile(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace("/", "_")
        if normalized not in TTS_PROFILES:
            raise ValueError(
                f"unknown TTS profile {value!r}; choose one of: {', '.join(sorted(TTS_PROFILES))}"
            )
        return normalized

    @model_validator(mode="after")
    def _enabled_profile(self) -> TTSConfig:
        if self.enabled and self.type == "none":
            raise ValueError("tts.type must name an engine when TTS is enabled")
        return self


class SafetyConfig(ConfigModel):
    max_run_time_ms: int = Field(default=2_000, ge=100, le=10_000)
    command_ttl_ms: int = Field(default=2_000, ge=100, le=30_000)
    stop_timeout_ms: int = Field(default=1_000, ge=50, le=5_000)
    command_queue_size: int = Field(default=64, ge=4, le=1_024)
    require_media_for_motion: bool = True


class StateConfig(ConfigModel):
    directory: Path | None = None
    device_key_file: str = Field(default="device-key", pattern=r"^[A-Za-z0-9_.-]+$")


class DiagnosticsConfig(ConfigModel):
    upload_enabled: bool = False
    buffer_lines: int = Field(default=400, ge=50, le=5_000)
    batch_lines: int = Field(default=50, ge=1, le=200)
    retention_sec: int = Field(default=900, ge=60, le=86_400)
    redaction_literals: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("redaction_literals")
    @classmethod
    def _safe_redaction_literals(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value or len(value) > 128 for value in normalized):
            raise ValueError("diagnostics redaction literals must contain 1-128 characters")
        return normalized


class TelemetryConfig(ConfigModel):
    operational_enabled: bool = False
    product_analytics_enabled: bool = False


class OtaConfig(ConfigModel):
    enabled: bool = False
    manifest_url: str | None = None
    public_key_file: Path | None = None
    state_directory: Path | None = None

    @model_validator(mode="after")
    def _complete_when_enabled(self) -> OtaConfig:
        if not self.enabled:
            return self
        missing = [
            name
            for name, value in (
                ("manifest_url", self.manifest_url),
                ("public_key_file", self.public_key_file),
                ("state_directory", self.state_directory),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"OTA requires: {', '.join(missing)}")
        assert self.manifest_url is not None
        parsed = urlsplit(self.manifest_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("ota.manifest_url must be HTTPS without user credentials")
        return self


class NormalizedCameraConfig(ConfigModel):
    id: str
    label: str
    role: Literal["primary", "secondary", "auxiliary"]
    enabled: bool = True
    publish_mode: Literal["always_on"] = "always_on"
    camera: CameraConfig
    video: VideoConfig


class RobotConfig(ConfigModel):
    _source_path: Path | None = PrivateAttr(default=None)

    server: ServerConfig
    camera: CameraConfig = Field(default_factory=CameraConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    video: VideoConfig = Field(default_factory=VideoConfig)
    cameras: list[CameraStreamConfig] = Field(default_factory=list, max_length=8)
    audio_source_camera_id: str | None = None
    tts: TTSConfig = Field(default_factory=TTSConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    state: StateConfig = Field(default_factory=StateConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    ota: OtaConfig = Field(default_factory=OtaConfig)

    @model_validator(mode="after")
    def _validate_camera_contract(self) -> RobotConfig:
        ids = [camera.id for camera in self.cameras]
        duplicates = sorted({camera_id for camera_id in ids if ids.count(camera_id) > 1})
        if duplicates:
            raise ValueError(f"camera ids must be unique; duplicates: {', '.join(duplicates)}")

        enabled = [camera for camera in self.cameras if camera.enabled]
        primary = [camera.id for camera in enabled if camera.role == "primary"]
        if len(primary) > 1:
            raise ValueError(
                f"only one enabled camera may have role 'primary': {', '.join(primary)}"
            )
        if self.audio_source_camera_id is not None:
            selected = self.audio_source_camera_id.strip().lower()
            if selected not in {camera.id for camera in enabled}:
                raise ValueError("audio_source_camera_id must reference an enabled camera id")
            self.audio_source_camera_id = selected
        return self


def normalize_cameras(config: RobotConfig) -> list[NormalizedCameraConfig]:
    if not config.cameras:
        return [
            NormalizedCameraConfig(
                id="front",
                label="Front",
                role="primary",
                camera=config.camera.model_copy(deep=True),
                video=config.video.model_copy(deep=True),
            )
        ]

    normalized: list[NormalizedCameraConfig] = []
    base_camera = config.camera.model_dump()
    base_video = config.video.model_dump()
    enabled_index = 0

    for entry in config.cameras:
        camera_data = dict(base_camera)
        for field_name in (
            "device",
            "width",
            "height",
            "fps",
            "backend",
            "fourcc",
            "buffer_size",
            "warmup_frames",
        ):
            value = getattr(entry, field_name)
            if value is not None:
                camera_data[field_name] = value

        video_data = dict(base_video)
        if entry.video is not None:
            if entry.video.type:
                video_data["type"] = entry.video.type
            if entry.video.options is not None:
                video_data["options"] = {
                    **dict(base_video.get("options", {})),
                    **entry.video.options,
                }
        video_data["options"] = {
            **dict(video_data.get("options", {})),
            "camera_id": entry.id,
        }

        default_role: Literal["primary", "secondary", "auxiliary"] = (
            "primary" if entry.enabled and enabled_index == 0 else "secondary"
        )
        normalized.append(
            NormalizedCameraConfig(
                id=entry.id,
                label=(entry.label or entry.id.replace("_", " ").replace("-", " ").title()),
                role=entry.role or default_role,
                enabled=entry.enabled,
                publish_mode=entry.publish_mode,
                camera=CameraConfig(**camera_data),
                video=VideoConfig(**video_data),
            )
        )
        if entry.enabled:
            enabled_index += 1
    return normalized
