"""Closed, bounded option schemas for built-in hardware, video and TTS profiles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ProfileOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, hide_input_in_errors=True)


class EmptyOptions(ProfileOptions):
    pass


class AutoHardwareOptions(ProfileOptions):
    auto_profile: str = Field(min_length=1, max_length=64)


class TimedDriveOptions(ProfileOptions):
    drive_time: float = Field(default=0.35, ge=0.01, le=2.0)
    turn_time: float = Field(default=0.15, ge=0.01, le=2.0)


class CozmoOptions(ProfileOptions):
    forward_speed: int = Field(default=75, ge=1, le=220)
    volume: int = Field(default=100, ge=0, le=100)
    colour: bool = True


class VectorOptions(CozmoOptions):
    turn_speed: int = Field(default=50, ge=1, le=220)
    serial: str | None = Field(default=None, min_length=1, max_length=128)


class L298nOptions(ProfileOptions):
    forward_pins: list[int] = Field(default_factory=list, max_length=8)
    backward_pins: list[int] = Field(default_factory=list, max_length=8)
    left_pins: list[int] = Field(default_factory=list, max_length=8)
    right_pins: list[int] = Field(default_factory=list, max_length=8)
    drive_seconds: float = Field(default=0.35, ge=0.01, le=2.0)
    turn_seconds: float = Field(default=0.2, ge=0.01, le=2.0)

    @model_validator(mode="after")
    def _pins_are_unique(self) -> L298nOptions:
        pins = self.forward_pins + self.backward_pins + self.left_pins + self.right_pins
        if any(pin < 0 or pin > 53 for pin in pins):
            raise ValueError("GPIO pins must be between 0 and 53")
        if len(pins) != len(set(pins)):
            raise ValueError("GPIO pins must not be duplicated")
        return self


class MaestroOptions(ProfileOptions):
    left_channel: int = Field(default=0, ge=0, le=23)
    right_channel: int = Field(default=1, ge=0, le=23)
    center: int = Field(default=6000, ge=2000, le=10000)
    forward: int = Field(default=10000, ge=2000, le=12000)
    backward: int = Field(default=2000, ge=0, le=10000)
    straight_delay: float = Field(default=0.35, ge=0.01, le=2.0)
    turn_delay: float = Field(default=0.2, ge=0.01, le=2.0)

    @model_validator(mode="after")
    def _channels_differ(self) -> MaestroOptions:
        if self.left_channel == self.right_channel:
            raise ValueError("left_channel and right_channel must differ")
        return self


class Max7219Options(ProfileOptions):
    rotate: Literal[0, 90, 180, 270] = 0
    bus: int = Field(default=0, ge=0, le=16)
    device: int = Field(default=0, ge=0, le=16)
    max_speed_hz: int = Field(default=1_000_000, ge=10_000, le=10_000_000)


class SpeedDriveOptions(ProfileOptions):
    driving_speed: int = Field(default=180, ge=1, le=255)
    drive_time: float = Field(default=0.3, ge=0.01, le=2.0)


class Mdd10Options(ProfileOptions):
    an1: int = Field(default=12, ge=0, le=53)
    an2: int = Field(default=13, ge=0, le=53)
    dig1: int = Field(default=26, ge=0, le=53)
    dig2: int = Field(default=24, ge=0, le=53)
    turn_delay: float = Field(default=0.2, ge=0.01, le=2.0)
    straight_delay: float = Field(default=0.35, ge=0.01, le=2.0)
    speed_percent: int = Field(default=60, ge=1, le=100)
    max_speed_percent: int = Field(default=100, ge=1, le=100)

    @model_validator(mode="after")
    def _pins_and_speed(self) -> Mdd10Options:
        if len({self.an1, self.an2, self.dig1, self.dig2}) != 4:
            raise ValueError("MDD10 GPIO pins must be unique")
        if self.speed_percent > self.max_speed_percent:
            raise ValueError("speed_percent must not exceed max_speed_percent")
        return self


class MegaPiOptions(ProfileOptions):
    motor_time: float = Field(default=0.2, ge=0.01, le=2.0)
    driving_speed: int = Field(default=150, ge=1, le=255)
    arm_speed: int = Field(default=50, ge=1, le=255)
    grabber_speed: int = Field(default=50, ge=1, le=255)
    left_track_port: int = Field(default=2, ge=1, le=4)
    right_track_port: int = Field(default=3, ge=1, le=4)
    grabber_port: int = Field(default=4, ge=1, le=4)
    arm_port: int = Field(default=1, ge=1, le=4)

    @model_validator(mode="after")
    def _ports_are_unique(self) -> MegaPiOptions:
        ports = [self.left_track_port, self.right_track_port, self.grabber_port, self.arm_port]
        if len(set(ports)) != len(ports):
            raise ValueError("MegaPi motor ports must be unique")
        return self


class MotoZeroOptions(ProfileOptions):
    motor_delay: float = Field(default=0.25, ge=0.01, le=2.0)
    motor1a: int = Field(default=24, ge=0, le=53)
    motor1b: int = Field(default=25, ge=0, le=53)
    motor1enable: int = Field(default=12, ge=0, le=53)
    motor2a: int = Field(default=27, ge=0, le=53)
    motor2b: int = Field(default=17, ge=0, le=53)
    motor2enable: int = Field(default=13, ge=0, le=53)
    motor3a: int = Field(default=6, ge=0, le=53)
    motor3b: int = Field(default=5, ge=0, le=53)
    motor3enable: int = Field(default=18, ge=0, le=53)
    motor4a: int = Field(default=22, ge=0, le=53)
    motor4b: int = Field(default=23, ge=0, le=53)
    motor4enable: int = Field(default=19, ge=0, le=53)

    @model_validator(mode="after")
    def _pins_are_unique(self) -> MotoZeroOptions:
        pins = [getattr(self, name) for name in type(self).model_fields if name != "motor_delay"]
        if len(pins) != len(set(pins)):
            raise ValueError("MotoZero GPIO pins must be unique")
        return self


class MotorHatOptions(ProfileOptions):
    drive_speed: int = Field(default=180, ge=1, le=255)
    turn_speed: int = Field(default=180, ge=1, le=255)
    drive_time: float = Field(default=0.35, ge=0.01, le=2.0)
    turn_time: float = Field(default=0.2, ge=0.01, le=2.0)
    left_motors: list[int] = Field(default_factory=lambda: [1, 2], min_length=1, max_length=2)
    right_motors: list[int] = Field(default_factory=lambda: [3, 4], min_length=1, max_length=2)
    up_motor: int = Field(default=0, ge=0, le=4)
    open_motor: int = Field(default=0, ge=0, le=4)
    address: int = Field(default=0x60, ge=0x03, le=0x77)
    i2c_bus: int | None = Field(default=None, ge=0, le=16)

    @model_validator(mode="after")
    def _motors_are_unique(self) -> MotorHatOptions:
        motors = self.left_motors + self.right_motors
        if any(motor < 1 or motor > 4 for motor in motors) or len(motors) != len(set(motors)):
            raise ValueError("drive motor channels must be unique values between 1 and 4")
        return self


class MqttOptions(ProfileOptions):
    host: str = Field(default="localhost", min_length=1, max_length=253)
    port: int | None = Field(default=None, ge=1, le=65535)
    topic: str = Field(default="botparty/robot/command", min_length=1, max_length=256)
    stop_topic: str | None = Field(default=None, min_length=1, max_length=256)
    status_topic: str = Field(default="botparty/robot/status", min_length=1, max_length=256)
    username: str | None = Field(default=None, max_length=256)
    password: SecretStr | None = None
    stop_command: str = Field(default="stop", min_length=1, max_length=128)
    payload_mode: Literal["plain", "json"] = "plain"
    qos: Literal[1, 2] = 1
    ack_timeout_sec: float = Field(default=1.0, ge=0.1, le=10.0)
    tls: bool | None = None
    ca_file: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _secure_transport(self) -> MqttOptions:
        loopback = self.host.lower() in {"localhost", "127.0.0.1", "::1"}
        if self.tls is False and not loopback:
            raise ValueError("remote MQTT hosts require TLS")
        if any(
            character in topic
            for topic in (self.topic, self.stop_topic or "")
            for character in "#+"
        ):
            raise ValueError("MQTT publish topics must not contain wildcard characters")
        return self


class NavQOptions(ProfileOptions):
    yaw_step: float = Field(default=45.0, ge=1.0, le=180.0)
    thrust: float = Field(default=0.1, ge=0.01, le=0.3)
    system_address: str = Field(
        default="serial:///dev/ttymxc2:921600", min_length=8, max_length=512
    )


class OwiArmOptions(ProfileOptions):
    step_seconds: float = Field(default=0.15, ge=0.01, le=1.0)
    vendor_id: int = Field(default=0x1267, ge=1, le=0xFFFF)
    product_id: int = Field(default=0, ge=0, le=0xFFFF)


class SerialOptions(ProfileOptions):
    device: str = Field(default="/dev/ttyUSB0", min_length=1, max_length=4096)
    baud_rate: int = Field(default=115200, ge=1200, le=4_000_000)
    device_name: str | None = Field(default=None, min_length=1, max_length=128)
    line_ending: Literal["", "\n", "\r", "\r\n"] = "\n"
    stop_command: str = Field(default="stop", min_length=1, max_length=128)
    payload_mode: Literal["plain", "json"] = "plain"
    protocol: Literal["legacy", "framed_v1"] = "legacy"
    write_timeout_sec: float = Field(default=1.0, ge=0.1, le=10.0)
    ack_timeout_sec: float = Field(default=1.0, ge=0.1, le=10.0)

    @field_validator("device")
    @classmethod
    def _device_path(cls, value: str) -> str:
        if not value.startswith("/dev/") or any(character in value for character in "\x00\r\n"):
            raise ValueError("serial device must be an absolute /dev path")
        return value


class ThunderBorgOptions(ProfileOptions):
    left_motor_max: float = Field(default=1.0, ge=0.0, le=1.0)
    right_motor_max: float = Field(default=1.0, ge=0.0, le=1.0)
    sleep_time: float = Field(default=0.3, ge=0.01, le=2.0)
    address: int | None = Field(default=None, ge=0x03, le=0x77)


class AdafruitPwmOptions(ProfileOptions):
    address: int | str = 0x40
    pwm_freq: int = Field(default=60, ge=24, le=1526)
    drive_channel: int = Field(default=0, ge=0, le=15)
    steer_channel: int = Field(default=1, ge=0, le=15)
    aux_channel: int = Field(default=2, ge=0, le=15)
    neutral_drive: int = Field(default=335, ge=0, le=4095)
    forward_drive: int = Field(default=445, ge=0, le=4095)
    forward_slow: int = Field(default=345, ge=0, le=4095)
    backward_drive: int = Field(default=270, ge=0, le=4095)
    backward_slow: int = Field(default=325, ge=0, le=4095)
    steer_left: int = Field(default=300, ge=0, le=4095)
    steer_center: int = Field(default=400, ge=0, le=4095)
    steer_right: int = Field(default=500, ge=0, le=4095)
    aux_increment: int = Field(default=300, ge=0, le=4095)
    aux_decrement: int = Field(default=400, ge=0, le=4095)
    aux_pos60: int = Field(default=490, ge=0, le=4095)
    aux_neg60: int = Field(default=100, ge=0, le=4095)

    @model_validator(mode="after")
    def _channels_are_unique(self) -> AdafruitPwmOptions:
        if len({self.drive_channel, self.steer_channel, self.aux_channel}) != 3:
            raise ValueError("PCA9685 channels must be unique")
        if isinstance(self.address, str):
            try:
                address = int(self.address, 0)
            except ValueError as exc:
                raise ValueError("address must be an I2C integer or numeric string") from exc
            if not 0x03 <= address <= 0x77:
                raise ValueError("I2C address must be between 0x03 and 0x77")
        return self


HARDWARE_OPTION_MODELS: dict[str, type[ProfileOptions]] = {
    "none": EmptyOptions,
    "auto": AutoHardwareOptions,
    "adafruit_pwm": AdafruitPwmOptions,
    "cozmo": CozmoOptions,
    "vector": VectorOptions,
    "gopigo2": TimedDriveOptions,
    "gopigo3": TimedDriveOptions,
    "l298n": L298nOptions,
    "maestro_servo": MaestroOptions,
    "max7219": Max7219Options,
    "mc33926": SpeedDriveOptions,
    "pololu": SpeedDriveOptions,
    "mdd10": Mdd10Options,
    "megapi_board": MegaPiOptions,
    "motozero": MotoZeroOptions,
    "motor_hat": MotorHatOptions,
    "mqtt_pub": MqttOptions,
    "navq": NavQOptions,
    "owi_arm": OwiArmOptions,
    "serial_board": SerialOptions,
    "telly": SerialOptions,
    "thunderborg": ThunderBorgOptions,
}


class VideoCommonOptions(ProfileOptions):
    camera_id: str | None = Field(default=None, min_length=1, max_length=32)
    publish_fps: int | None = Field(default=None, ge=1, le=120)
    target_bitrate_kbps: int = Field(default=1200, ge=100, le=50000)
    video_codec: str | None = Field(default=None, min_length=1, max_length=128)
    ffmpeg_path: str = Field(default="ffmpeg", min_length=1, max_length=4096)
    input_format: str = Field(default="", max_length=32)
    input_driver: str = Field(default="v4l2", min_length=1, max_length=32)
    loglevel: Literal["quiet", "panic", "fatal", "error", "warning", "info"] = "error"
    analyzeduration: int = Field(default=0, ge=0, le=10_000_000)
    probesize: int = Field(default=32, ge=32, le=10_000_000)
    fpsprobesize: int = Field(default=0, ge=0, le=10_000)
    thread_queue_size: int = Field(default=2, ge=1, le=1024)
    publisher_binary: str | None = Field(default=None, min_length=1, max_length=4096)
    botparty_streamer_path: str | None = Field(default=None, min_length=1, max_length=4096)
    lk_h264_publisher_path: str | None = Field(default=None, min_length=1, max_length=4096)
    publisher_binary_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    publisher_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class AudioVideoOptions(VideoCommonOptions):
    audio_sample_rate: int = Field(default=48000, ge=8000, le=192000)
    audio_channels: Literal[1, 2] = 1
    audio_chunk_ms: int = Field(default=40, ge=10, le=500)
    audio_queue_frames: int = Field(default=8, ge=1, le=256)
    arecord_path: str = Field(default="arecord", min_length=1, max_length=4096)
    audio_device: str = Field(default="default", min_length=1, max_length=128)
    arecord_format: Literal["S16_LE", "S24_LE", "S32_LE"] = "S16_LE"


class LibcameraOptions(VideoCommonOptions):
    libcamera_path: str = Field(default="libcamera-vid", min_length=1, max_length=4096)


class DirectPublisherOptions(AudioVideoOptions):
    track_name: str | None = Field(default=None, min_length=1, max_length=128)
    publisher_tcp_port: int | None = Field(default=None, ge=1024, le=65535)
    publisher_tcp_port_base: int = Field(default=5600, ge=1024, le=65000)
    livekit_identity: str | None = Field(default=None, min_length=1, max_length=128)
    livekit_room: str = Field(default="robot-room", min_length=1, max_length=128)
    gop_frames: int | None = Field(default=None, ge=1, le=1200)
    stats_period_sec: int = Field(default=5, ge=1, le=60)
    frame_chan_size: int = Field(default=4, ge=1, le=256)
    max_publish_stale_ms: int = Field(default=250, ge=10, le=10000)
    au_max_nalus: int = Field(default=64, ge=1, le=1024)
    au_max_bytes: int = Field(default=2_097_152, ge=65536, le=16_777_216)
    input_read_timeout_ms: int = Field(default=500, ge=50, le=30000)
    frame_flush_timeout_ms: int = Field(default=50, ge=5, le=5000)
    reconnect_min_ms: int = Field(default=250, ge=50, le=30000)
    reconnect_max_ms: int = Field(default=4000, ge=100, le=120000)

    @model_validator(mode="after")
    def _reconnect_range(self) -> DirectPublisherOptions:
        if self.reconnect_min_ms > self.reconnect_max_ms:
            raise ValueError("reconnect_min_ms must not exceed reconnect_max_ms")
        return self


class AnnotatedVideoOptions(ProfileOptions):
    camera_id: str | None = Field(default=None, min_length=1, max_length=32)
    publish_fps: int | None = Field(default=None, ge=1, le=120)
    annotated: bool = False


VIDEO_OPTION_MODELS: dict[str, type[ProfileOptions]] = {
    "none": EmptyOptions,
    "opencv": AnnotatedVideoOptions,
    "cozmo_vid": AnnotatedVideoOptions,
    "vector_vid": AnnotatedVideoOptions,
    "ffmpeg": VideoCommonOptions,
    "ffmpeg_hud": VideoCommonOptions,
    "ffmpeg_arecord": AudioVideoOptions,
    "ffmpeg_libcamera": LibcameraOptions,
    "botparty_streamer": DirectPublisherOptions,
}


class TtsCommonOptions(ProfileOptions):
    cloud_data_processing_accepted: bool = False


class EspeakOptions(TtsCommonOptions):
    espeak_path: str = Field(default="espeak", min_length=1, max_length=4096)
    aplay_path: str = Field(default="aplay", min_length=1, max_length=4096)
    voice: str = Field(default="en-us", min_length=1, max_length=64)
    voice_variant: str = Field(default="m1", min_length=1, max_length=32)
    speed: int = Field(default=170, ge=80, le=450)


class FestivalOptions(TtsCommonOptions):
    text2wave_path: str = Field(default="text2wave", min_length=1, max_length=4096)
    aplay_path: str = Field(default="aplay", min_length=1, max_length=4096)


class PicoOptions(TtsCommonOptions):
    pico2wave_path: str = Field(default="pico2wave", min_length=1, max_length=4096)
    aplay_path: str = Field(default="aplay", min_length=1, max_length=4096)
    voice: str = Field(default="en-US", min_length=1, max_length=32)


class GoogleOptions(TtsCommonOptions):
    aplay_path: str = Field(default="aplay", min_length=1, max_length=4096)
    key_file: str | None = Field(default=None, min_length=1, max_length=4096)
    voice: str = Field(default="en-US-Neural2-F", min_length=1, max_length=128)
    language_code: str | None = Field(default=None, min_length=2, max_length=16)
    voice_pitch: float = Field(default=0.0, ge=-20.0, le=20.0)
    voice_speaking_rate: float = Field(default=1.0, ge=0.25, le=4.0)
    ssml_enabled: bool = False


class PollyOptions(TtsCommonOptions):
    mpg123_path: str = Field(default="mpg123", min_length=1, max_length=4096)
    output_module: Literal["", "alsa", "pulse"] = ""
    robot_voice: str = Field(default="Amy", min_length=1, max_length=64)
    region_name: str = Field(default="eu-central-1", min_length=3, max_length=64)
    access_key_file: str | None = Field(default=None, min_length=1, max_length=4096)
    secret_key_file: str | None = Field(default=None, min_length=1, max_length=4096)


TTS_OPTION_MODELS: dict[str, type[ProfileOptions]] = {
    "none": EmptyOptions,
    "cozmo_tts": TtsCommonOptions,
    "vector_tts": TtsCommonOptions,
    "espeak": EspeakOptions,
    "festival": FestivalOptions,
    "pico": PicoOptions,
    "google_cloud": GoogleOptions,
    "polly": PollyOptions,
}


OPTION_MODEL_REGISTRIES: dict[str, Mapping[str, type[ProfileOptions]]] = {
    "hardware": HARDWARE_OPTION_MODELS,
    "video": VIDEO_OPTION_MODELS,
    "tts": TTS_OPTION_MODELS,
}


def validate_profile_options(kind: str, profile: str, options: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a built-in profile's option dictionary."""

    if profile == "custom":
        if kind not in {"hardware", "tts"}:
            raise ValueError(f"custom {kind} profiles are not supported")
        if len(options) > 64:
            raise ValueError(f"custom {kind} options must contain at most 64 entries")
        return dict(options)
    model = OPTION_MODEL_REGISTRIES[kind].get(profile)
    if model is None:
        raise ValueError(f"{kind} profile {profile!r} has no option schema")
    validated = model.model_validate(options)
    result = validated.model_dump(exclude_unset=True)
    for key, value in tuple(result.items()):
        if isinstance(value, SecretStr):
            result[key] = value.get_secret_value()
    return result


def profile_options_schema() -> dict[str, object]:
    """Return stable JSON schemas for documentation and editor integrations."""

    return {
        kind: {profile: model.model_json_schema() for profile, model in sorted(registry.items())}
        for kind, registry in OPTION_MODEL_REGISTRIES.items()
    }
