import asyncio
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from botparty_robot.config import (
    CameraConfig,
    HardwareConfig,
    RobotConfig,
    ServerConfig,
    TTSConfig,
    VideoConfig,
)
from botparty_robot.operator import write_config_schema
from botparty_robot.video.botparty_streamer import VideoProfile


def test_builtin_profile_options_are_closed_and_bounded() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        HardwareConfig(type="mdd10", options={"unknown_pin": 12})
    with pytest.raises(ValidationError, match="less than or equal to 100"):
        HardwareConfig(type="mdd10", options={"speed_percent": 101})
    with pytest.raises(ValidationError, match="must be unique"):
        HardwareConfig(type="mdd10", options={"an1": 12, "an2": 12})
    with pytest.raises(ValidationError, match="must differ"):
        HardwareConfig(
            type="maestro_servo",
            options={"left_channel": 1, "right_channel": 1},
        )
    with pytest.raises(ValidationError, match="must not be duplicated"):
        HardwareConfig(
            type="l298n",
            options={"forward_pins": [12], "backward_pins": [12]},
        )


def test_transport_and_device_options_fail_during_config_validation() -> None:
    with pytest.raises(ValidationError, match="remote MQTT hosts require TLS"):
        HardwareConfig(
            type="mqtt_pub",
            options={"host": "mqtt.example.com", "tls": False},
        )
    with pytest.raises(ValidationError, match="less than or equal to 65535"):
        HardwareConfig(type="mqtt_pub", options={"port": 70000})
    with pytest.raises(ValidationError, match="must not contain wildcard"):
        HardwareConfig(type="mqtt_pub", options={"topic": "robots/+/command"})
    with pytest.raises(ValidationError, match="absolute /dev path"):
        HardwareConfig(type="serial_board", options={"device": "ttyUSB0"})


def test_video_and_tts_options_are_profile_specific() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        VideoConfig(type="ffmpeg", options={"voice": "unexpected"})
    with pytest.raises(ValidationError, match="less than or equal to 450"):
        TTSConfig(enabled=True, type="espeak", options={"speed": 500})
    google = TTSConfig(
        enabled=True,
        type="google_cloud",
        options={"key_file": "/run/credentials/google.json"},
    )
    assert google.options["key_file"] == "/run/credentials/google.json"
    polly = TTSConfig(
        enabled=True,
        type="polly",
        options={
            "access_key_file": "/run/credentials/aws-access-key",
            "secret_key_file": "/run/credentials/aws-secret-key",
        },
    )
    assert polly.options == {
        "access_key_file": "/run/credentials/aws-access-key",
        "secret_key_file": "/run/credentials/aws-secret-key",
    }
    with pytest.raises(ValidationError, match="numeric indices are only supported by opencv"):
        RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            camera=CameraConfig(device=0),
            video=VideoConfig(type="ffmpeg"),
        )
    assert RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        camera=CameraConfig(device=0),
        video=VideoConfig(type="opencv"),
    )


def test_generated_schema_contains_every_builtin_profile(tmp_path: Path) -> None:
    output = tmp_path / "robot-config.schema.json"
    write_config_schema(output)
    text = output.read_text(encoding="utf-8")
    assert '"schemaVersion": 1' in text
    for profile in ("mqtt_pub", "serial_board", "botparty_streamer", "google_cloud"):
        assert f'"{profile}"' in text


def test_direct_publisher_does_not_pass_token_to_ffmpeg(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        binary = tmp_path / "botparty-streamer"
        binary.write_bytes(Path("/bin/true").read_bytes())
        binary.chmod(0o700)
        digest = hashlib.sha256(binary.read_bytes()).hexdigest()
        config = RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            video=VideoConfig(
                type="botparty_streamer",
                options={
                    "publisher_binary": str(binary),
                    "publisher_sha256": digest,
                },
            ),
        )
        profile = VideoProfile(config)
        monkeypatch.setattr(profile, "command_exists", lambda _path: True)
        monkeypatch.setattr(profile, "verify_streamer_binary", lambda _path: digest)
        monkeypatch.setattr(profile, "detect_default_h264_codec", lambda: "libx264")
        monkeypatch.setattr(profile, "_tcp_port", lambda: 5601)

        calls: list[dict[str, object]] = []

        class Process:
            def __init__(self) -> None:
                self.returncode = None
                self.stderr = None
                self.stdout = None
                self.pid = 999999

            async def wait(self) -> int:
                self.returncode = 0
                return 0

            def send_signal(self, _signal) -> None:
                self.returncode = 0

        async def fake_spawn(*command, **kwargs):
            calls.append({"command": command, **kwargs})
            return Process()

        async def no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
        monkeypatch.setattr("botparty_robot.video.botparty_streamer.asyncio.sleep", no_sleep)
        process = await profile.spawn_livekit_process(
            livekit_url="wss://livekit.example",
            token="signed-publish-token",
            target_bitrate_kbps=900,
        )
        await process.wait()

        assert len(calls) == 2
        publisher_env = calls[0]["env"]
        ffmpeg_env = calls[1]["env"]
        assert (
            isinstance(publisher_env, dict) and publisher_env["LK_TOKEN"] == "signed-publish-token"
        )
        assert isinstance(ffmpeg_env, dict) and "LK_TOKEN" not in ffmpeg_env
        assert "signed-publish-token" not in " ".join(calls[1]["command"])

    asyncio.run(scenario())
