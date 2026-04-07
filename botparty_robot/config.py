"""Configuration models for the robot client."""

from typing import Any

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    api_url: str = "https://botparty.live"
    livekit_url: str = "wss://botparty.live/rtc"
    claim_token: str


class CameraConfig(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = 30
    device: str | int = "/dev/video0"
    backend: str = "v4l2"
    fourcc: str | None = "MJPG"
    buffer_size: int = Field(default=1, ge=1, le=8)
    warmup_frames: int = Field(default=4, ge=0, le=30)


class ControlsConfig(BaseModel):
    gpio_enabled: bool = False
    motor_left_forward: int = 17
    motor_left_backward: int = 18
    motor_right_forward: int = 22
    motor_right_backward: int = 23
    servo_camera_pan: int | None = None
    servo_camera_tilt: int | None = None


class HardwareConfig(BaseModel):
    type: str = "none"
    options: dict[str, Any] = Field(default_factory=dict)


class VideoConfig(BaseModel):
    type: str = "ffmpeg"
    options: dict[str, Any] = Field(default_factory=dict)


class CameraVideoOverrideConfig(BaseModel):
    type: str | None = None
    options: dict[str, Any] | None = None


class CameraStreamConfig(BaseModel):
    id: str
    label: str | None = None
    role: str | None = None
    enabled: bool = True
    publish_mode: str = "always_on"
    device: str | int | None = None
    width: int | None = None
    height: int | None = None
    fps: int | None = None
    backend: str | None = None
    fourcc: str | None = None
    buffer_size: int | None = Field(default=None, ge=1, le=8)
    warmup_frames: int | None = Field(default=None, ge=0, le=30)
    video: CameraVideoOverrideConfig | None = None


class TTSConfig(BaseModel):
    enabled: bool = False
    type: str = "none"
    playback_device: str = "default"
    volume: int = Field(default=70, ge=0, le=100)
    chat_to_tts: bool = True
    filter_urls: bool = False
    allow_anonymous: bool = True
    blocked_senders: list[str] = Field(default_factory=list)
    delay_ms: int = Field(default=0, ge=0, le=30000)
    options: dict[str, Any] = Field(default_factory=dict)


class SafetyConfig(BaseModel):
    emergency_stop_pin: int | None = None
    max_run_time_ms: int = Field(default=2000, ge=500, le=10000)


class NormalizedCameraConfig(BaseModel):
    id: str
    label: str
    role: str
    enabled: bool = True
    publish_mode: str = "always_on"
    camera: CameraConfig
    video: VideoConfig


class RobotConfig(BaseModel):
    server: ServerConfig
    camera: CameraConfig = CameraConfig()
    controls: ControlsConfig = ControlsConfig()
    hardware: HardwareConfig = HardwareConfig()
    video: VideoConfig = VideoConfig()
    cameras: list[CameraStreamConfig] = Field(default_factory=list)
    tts: TTSConfig = TTSConfig()
    safety: SafetyConfig = SafetyConfig()


def normalize_cameras(config: RobotConfig) -> list[NormalizedCameraConfig]:
    if not config.cameras:
        return [
            NormalizedCameraConfig(
                id="front",
                label="Front",
                role="primary",
                enabled=True,
                publish_mode="always_on",
                camera=config.camera.model_copy(deep=True),
                video=config.video.model_copy(deep=True),
            )
        ]

    normalized: list[NormalizedCameraConfig] = []
    seen_ids: set[str] = set()
    base_camera = config.camera.model_dump()
    base_video = config.video.model_dump()

    for index, entry in enumerate(config.cameras):
        camera_id = entry.id.strip()
        if not camera_id or camera_id in seen_ids:
            continue
        seen_ids.add(camera_id)

        camera_data = dict(base_camera)
        for field in ("device", "width", "height", "fps", "backend", "fourcc", "buffer_size", "warmup_frames"):
            value = getattr(entry, field)
            if value is not None:
                camera_data[field] = value

        video_data = dict(base_video)
        video_override = entry.video
        if video_override is not None:
            if video_override.type:
                video_data["type"] = video_override.type
            if video_override.options is not None:
                video_data["options"] = {
                    **dict(base_video.get("options", {})),
                    **dict(video_override.options),
                }

        normalized.append(
            NormalizedCameraConfig(
                id=camera_id,
                label=(entry.label or camera_id.replace("_", " ").replace("-", " ").title()).strip() or camera_id,
                role=(entry.role or ("primary" if index == 0 else "secondary")).strip(),
                enabled=entry.enabled,
                publish_mode=(entry.publish_mode or ("always_on" if index == 0 else "preview_only")).strip(),
                camera=CameraConfig(**camera_data),
                video=VideoConfig(**video_data),
            )
        )

    return normalized
