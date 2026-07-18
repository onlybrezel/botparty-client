from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from botparty_robot.config import HardwareConfig, RobotConfig, ServerConfig, StateConfig, TTSConfig
from botparty_robot.profile_options import HARDWARE_OPTION_MODELS, TTS_OPTION_MODELS
from botparty_robot.safety import SafetyController

FAKE_BACKEND_PROFILES = set(HARDWARE_OPTION_MODELS) - {"auto", "none"}


def _config(profile: str) -> RobotConfig:
    options: dict[str, object] = {}
    if profile == "l298n":
        options = {
            "forward_pins": [1],
            "backward_pins": [2],
            "left_pins": [3],
            "right_pins": [4],
        }
    return RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        hardware=HardwareConfig(type=profile, options=options),
    )


def _install_fake_backend(profile: str, adapter: object) -> list[MagicMock]:
    primary = MagicMock(name=f"{profile}-backend")
    if profile == "adafruit_pwm":
        adapter.pwm = primary
    elif profile == "cozmo":
        adapter.cozmo = SimpleNamespace(
            util=SimpleNamespace(
                degrees=lambda value: value,
                distance_mm=lambda value: value,
                speed_mmps=lambda value: value,
            )
        )
        adapter.robot = primary
    elif profile == "gopigo2":
        adapter.gopigo = primary
    elif profile == "gopigo3":
        primary.get_speed.return_value = 100
        primary.MOTOR_LEFT = 1
        primary.MOTOR_RIGHT = 2
        adapter.robot = primary
    elif profile == "l298n":
        primary.HIGH = 1
        primary.LOW = 0
        adapter.gpio = primary
    elif profile == "maestro_servo":
        adapter.controller = primary
    elif profile == "max7219":
        adapter.spi = primary
    elif profile in {"mc33926", "pololu"}:
        adapter.motors = primary
    elif profile == "mdd10":
        adapter.gpio = primary
        adapter.p1 = MagicMock(name="mdd10-pwm-left")
        adapter.p2 = MagicMock(name="mdd10-pwm-right")
        return [primary, adapter.p1, adapter.p2]
    elif profile == "megapi_board":
        adapter.bot = primary
    elif profile == "motor_hat":
        motor = MagicMock(name="motor-hat-channel")
        primary.getMotor.return_value = motor
        adapter.mh = primary
        adapter.module = SimpleNamespace(
            Adafruit_MotorHAT=SimpleNamespace(FORWARD=1, BACKWARD=2, RELEASE=3)
        )
        return [primary, motor]
    elif profile == "motozero":
        primary.HIGH = 1
        primary.LOW = 0
        adapter.gpio = primary
    elif profile == "mqtt_pub":
        primary.is_connected.return_value = True
        publish = MagicMock(name="mqtt-publish-result")
        publish.rc = 0
        publish.is_published.return_value = True
        primary.publish.return_value = publish
        adapter.client = primary
        return [primary, publish]
    elif profile == "navq":
        adapter.rover = primary
        schedule = MagicMock(name="navq-schedule")

        def consume(coroutine: object) -> None:
            coroutine.close()

        schedule.side_effect = consume
        adapter._schedule = schedule
        return [schedule]
    elif profile == "owi_arm":
        adapter.arm = primary
    elif profile in {"serial_board", "telly"}:
        primary.is_open = True
        primary.write.side_effect = lambda payload: len(payload)
        primary.readline.return_value = b""
        adapter.serial = primary
    elif profile == "thunderborg":
        adapter.board = primary
    elif profile == "vector":
        adapter.vector = primary
    else:  # pragma: no cover - table completeness assertion identifies new profiles
        raise AssertionError(f"missing fake backend for {profile}")
    return [primary]


@pytest.mark.parametrize("profile", sorted(FAKE_BACKEND_PROFILES))
def test_every_hardware_profile_has_positive_invalid_stop_and_cleanup_contract(
    profile: str,
) -> None:
    module = importlib.import_module(f"botparty_robot.hardware.{profile}")
    adapter = module.HardwareAdapter(_config(profile))
    adapter.interruptible_sleep = lambda _seconds: None
    backends = _install_fake_backend(profile, adapter)
    command = (
        adapter.motion_commands[0] if adapter.motion_commands else adapter.supported_commands[0]
    )
    controller = SafetyController()

    adapter.execute(controller.issue_permit(), command, 25, {"actionId": "positive"})
    assert sum(len(backend.mock_calls) for backend in backends) > 0

    calls_after_positive = sum(len(backend.mock_calls) for backend in backends)
    with pytest.raises(ValueError, match="unsupported hardware command"):
        adapter.execute(controller.issue_permit(), "not-a-real-command")
    assert sum(len(backend.mock_calls) for backend in backends) == calls_after_positive

    adapter.apply_emergency_stop()
    if profile == "navq":
        assert adapter._loop is None
    else:
        assert sum(len(backend.mock_calls) for backend in backends) > calls_after_positive

    adapter.close()
    calls_after_close = sum(len(backend.mock_calls) for backend in backends)
    adapter.close()
    assert sum(len(backend.mock_calls) for backend in backends) == calls_after_close


def test_fake_backend_table_covers_every_builtin_hardware_profile() -> None:
    assert {
        "adafruit_pwm",
        "cozmo",
        "gopigo2",
        "gopigo3",
        "l298n",
        "maestro_servo",
        "max7219",
        "mc33926",
        "mdd10",
        "megapi_board",
        "motor_hat",
        "motozero",
        "mqtt_pub",
        "navq",
        "owi_arm",
        "pololu",
        "serial_board",
        "telly",
        "thunderborg",
        "vector",
    } == FAKE_BACKEND_PROFILES


def test_supported_none_profile_is_non_moving_and_idempotently_closable() -> None:
    module = importlib.import_module("botparty_robot.hardware.none")
    adapter = module.HardwareAdapter(_config("none"))

    assert adapter.capabilities().support_level == "supported"
    assert adapter.capabilities().motion_commands == ()
    with pytest.raises(ValueError, match="unsupported hardware command"):
        adapter.execute(SafetyController().issue_permit(), "forward")
    adapter.apply_emergency_stop()
    adapter.close()
    adapter.close()


@pytest.mark.parametrize("profile_name", sorted(set(TTS_OPTION_MODELS) - {"none"}))
def test_every_tts_profile_has_positive_filter_and_cancel_contract(
    profile_name: str, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setattr("botparty_robot.tts.base.set_alsa_volume", lambda *_args: None)
    module = importlib.import_module(f"botparty_robot.tts.{profile_name}")
    options = (
        {"cloud_data_processing_accepted": True}
        if profile_name in {"google_cloud", "polly"}
        else {}
    )
    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        state=StateConfig(directory=tmp_path / profile_name),
        tts=TTSConfig(
            enabled=True,
            type=profile_name,
            allow_anonymous=True,
            daily_character_budget=10_000,
            options=options,
        ),
    )
    profile = module.TTSProfile(config)
    backend = MagicMock(name=f"{profile_name}-tts-backend")

    if profile_name == "cozmo_tts":
        monkeypatch.setattr(module, "get_cozmo_robot", lambda: backend)
    elif profile_name == "vector_tts":
        monkeypatch.setattr(module, "get_vector_robot", lambda: backend)
    elif profile_name in {"espeak", "festival", "pico"}:
        profile.setup()
        monkeypatch.setattr(module, "command_exists", lambda _path: True)
        runner = "run_pipeline" if profile_name == "espeak" else "run_process"
        monkeypatch.setattr(module, runner, backend)
    elif profile_name == "google_cloud":
        profile.texttospeech = SimpleNamespace(
            SynthesisInput=MagicMock(),
            VoiceSelectionParams=MagicMock(),
            AudioConfig=MagicMock(),
            AudioEncoding=SimpleNamespace(LINEAR16="LINEAR16"),
        )
        profile.client = backend
        backend.synthesize_speech.return_value = SimpleNamespace(audio_content=b"wave")
        profile.aplay_path = "aplay"
        profile.voice = "en-US-Test"
        profile.language_code = "en-US"
        profile.pitch = 0.0
        profile.speaking_rate = 1.0
        profile.ssml_enabled = False
        monkeypatch.setattr(module, "command_exists", lambda _path: True)
        monkeypatch.setattr(module, "run_process", MagicMock())
    elif profile_name == "polly":
        profile.boto3 = object()
        profile.client = backend
        stream = MagicMock()
        stream.read.return_value = b"mp3"
        backend.synthesize_speech.return_value = {"AudioStream": stream}
        profile.mpg123_path = "mpg123"
        profile.output_module = "alsa"
        profile.voice = "Amy"
        monkeypatch.setattr(module, "command_exists", lambda _path: True)
        monkeypatch.setattr(module, "run_process", MagicMock())
    else:  # pragma: no cover - registry assertion below identifies new profiles
        raise AssertionError(f"missing TTS behavior fixture for {profile_name}")

    assert profile.can_handle() is True
    assert profile.should_speak("https://blocked.invalid", {"sender": "viewer"}) is False
    assert profile.last_rejection_code == "tts_url_blocked"
    assert profile.should_speak("hello", {"sender": "viewer"}) is True
    profile.run_say("hello", {"sender": "viewer"})
    assert backend.mock_calls

    profile.cancel_active()
    profile.mute()
    assert profile.can_handle() is False
    profile.unmute()
    assert profile.can_handle() is True


def test_none_tts_profile_is_explicitly_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("botparty_robot.tts.base.set_alsa_volume", lambda *_args: None)
    module = importlib.import_module("botparty_robot.tts.none")
    profile = module.TTSProfile(
        RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            state=StateConfig(directory=tmp_path),
            tts=TTSConfig(enabled=False, type="none"),
        )
    )

    assert profile.can_handle() is False
    assert profile.should_speak("hello", {"sender": "viewer"}) is False
    assert profile.last_rejection_code == "tts_disabled"
    profile.run_say("hello")
    profile.cancel_active()


def test_custom_hardware_delegates_command_and_lifecycle_contract(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("hardware: custom\n", encoding="utf-8")
    config_path.chmod(0o600)
    implementation = tmp_path / "hardware_custom.py"
    implementation.write_text(
        "from botparty_robot.hardware.base import BaseHardware\n"
        "class HardwareAdapter(BaseHardware):\n"
        "    profile_name = 'fixture'\n"
        "    supported_commands = ('ping',)\n"
        "    motion_commands = ()\n"
        "    def __init__(self, config):\n"
        "        super().__init__(config)\n"
        "        self.events = []\n"
        "    def setup(self): self.events.append('setup')\n"
        "    def on_command(self, command, value=None): self.events.append((command, value))\n"
        "    def emergency_stop(self): self.events.append('stop')\n"
        "    def _close_resources(self): self.events.append('close')\n",
        encoding="utf-8",
    )
    implementation.chmod(0o600)
    config = RobotConfig(
        server=ServerConfig(claim_token="claim-token"),
        hardware=HardwareConfig(type="custom", options={}),
    )
    config._source_path = config_path
    module = importlib.import_module("botparty_robot.hardware.custom")
    adapter = module.HardwareAdapter(config)

    adapter.start()
    adapter.execute(SafetyController().issue_permit(), "ping", 42)
    with pytest.raises(ValueError, match="unsupported hardware command"):
        adapter.execute(SafetyController().issue_permit(), "unknown")
    adapter.apply_emergency_stop()
    adapter.close()

    assert adapter.inner.events == [
        "setup",
        ("ping", 42),
        "stop",
        "stop",
        "stop",
        "close",
    ]


def test_custom_tts_delegates_positive_and_operator_controls(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("botparty_robot.tts.base.set_alsa_volume", lambda *_args: None)

    class FixtureTTS:
        def __init__(self, _config) -> None:
            self.events: list[object] = []

        def setup(self) -> None:
            self.events.append("setup")

        def can_handle(self) -> bool:
            return True

        def say(self, message, metadata) -> None:
            self.events.append((message, metadata))

        def mute(self) -> None:
            self.events.append("mute")

        def unmute(self) -> None:
            self.events.append("unmute")

        def set_volume(self, level) -> None:
            self.events.append(("volume", level))

    fixture_module = SimpleNamespace(FixtureTTS=FixtureTTS)
    monkeypatch.setitem(sys.modules, "fixture_tts_module", fixture_module)
    module = importlib.import_module("botparty_robot.tts.custom")
    profile = module.TTSProfile(
        RobotConfig(
            server=ServerConfig(claim_token="claim-token"),
            state=StateConfig(directory=tmp_path),
            tts=TTSConfig(
                enabled=True,
                type="custom",
                options={"class": "fixture_tts_module.FixtureTTS"},
            ),
        )
    )

    profile.setup()
    profile.run_say("hello", {"sender": "viewer"})
    profile.mute()
    profile.unmute()
    profile.set_volume(150)

    assert profile.inner.events == [
        "setup",
        ("hello", {"sender": "viewer"}),
        "mute",
        "unmute",
        ("volume", 100),
    ]
