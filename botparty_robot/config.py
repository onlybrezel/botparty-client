"""Configuration models for the robot client."""

from typing import Any

from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    api_url: str = "http://localhost:4000"
    livekit_url: str = "ws://localhost:7880"
    claim_token: str


class CameraConfig(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = 30
    device: str = "/dev/video0"
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
    latency_threshold_ms: int = Field(default=300, ge=100, le=1000)


class RobotConfig(BaseModel):
    server: ServerConfig
    camera: CameraConfig = CameraConfig()
    controls: ControlsConfig = ControlsConfig()
    hardware: HardwareConfig = HardwareConfig()
    video: VideoConfig = VideoConfig()
    tts: TTSConfig = TTSConfig()
    safety: SafetyConfig = SafetyConfig()
