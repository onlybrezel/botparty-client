import importlib
import inspect
import pkgutil
import threading
import time

import pytest

from botparty_robot import hardware as hardware_package
from botparty_robot.capabilities import build_capability_manifest
from botparty_robot.client_commands import ClientCommandsMixin
from botparty_robot.config import RobotConfig, ServerConfig
from botparty_robot.hardware.base import BaseHardware
from botparty_robot.safety import HardwareCommandCancelled, SafetyController, SafetyLatchedError
from botparty_robot.tts.base import BaseTTSProfile


class _RaceAdapter(BaseHardware):
    profile_name = "race"
    supported_commands = ("forward", "stop")
    motion_commands = ("forward",)
    safe_stop_capable = True

    def __init__(self, config: RobotConfig) -> None:
        super().__init__(config)
        self.first_write = threading.Event()
        self.release = threading.Event()
        self.writes: list[str] = []

    def on_command(self, command: str, value: object = None) -> None:
        del command, value
        self.guarded_write(lambda: self.writes.append("active:first"))
        self.first_write.set()
        self.release.wait(timeout=2)
        self.guarded_write(lambda: self.writes.append("active:second"))

    def emergency_stop(self) -> None:
        self.writes.append("safe:stop")


def _config() -> RobotConfig:
    return RobotConfig(server=ServerConfig(claim_token="claim-token"))


def test_stop_prevents_every_later_active_write_and_stays_latched() -> None:
    controller = SafetyController()
    adapter = _RaceAdapter(_config())
    errors: list[BaseException] = []

    def execute() -> None:
        try:
            adapter.execute(controller.issue_permit(), "forward")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=execute)
    worker.start()
    assert adapter.first_write.wait(timeout=1)

    controller.stop("test")
    adapter.apply_emergency_stop()
    adapter.release.set()
    worker.join(timeout=2)

    assert adapter.writes == ["active:first", "safe:stop"]
    assert len(errors) == 1
    assert isinstance(errors[0], HardwareCommandCancelled)
    with pytest.raises(SafetyLatchedError):
        controller.issue_permit()

    controller.reset()
    assert controller.issue_permit().epoch == 2


def test_interruptible_wait_reacts_without_polling_delay() -> None:
    controller = SafetyController()
    permit = controller.issue_permit()
    finished = threading.Event()

    def wait() -> None:
        with pytest.raises(HardwareCommandCancelled):
            permit.wait(30)
        finished.set()

    worker = threading.Thread(target=wait)
    worker.start()
    time.sleep(0.01)
    controller.stop("deadline")
    assert finished.wait(timeout=0.2)
    worker.join(timeout=1)


def test_adapter_capability_contract_is_explicit() -> None:
    adapter = _RaceAdapter(_config())
    capabilities = adapter.capabilities()
    assert capabilities.safe_stop is True
    assert capabilities.close is True
    assert "stop" in capabilities.commands
    adapter.close()
    adapter.close()
    assert adapter.writes == ["safe:stop"]


def test_every_registered_adapter_implements_the_lifecycle_contract() -> None:
    excluded = {"base", "common", "gpio"}
    names = {
        module.name
        for module in pkgutil.iter_modules(hardware_package.__path__)
        if module.name not in excluded and not module.name.startswith("_")
    }
    assert names
    for name in sorted(names):
        module = importlib.import_module(f"botparty_robot.hardware.{name}")
        adapter_type = module.HardwareAdapter
        assert issubclass(adapter_type, BaseHardware), name
        assert not inspect.isabstract(adapter_type), name
        assert adapter_type.profile_name != "base", name
        assert callable(adapter_type.start), name
        assert adapter_type.support_level in {"supported", "community", "experimental"}, name
        commands = set(adapter_type.supported_commands)
        motion_commands = set(adapter_type.motion_commands)
        assert motion_commands <= commands, name
        if motion_commands:
            assert "stop" in commands, name


def test_motion_deadline_uses_exact_monotonic_schedule_and_aliases(monkeypatch) -> None:
    scheduled: dict[str, object] = {}

    class _Handle:
        def cancel(self) -> None:
            scheduled["cancelled"] = True

    class _Loop:
        def time(self) -> float:
            return 10.0

        def call_at(self, deadline: float, callback):
            scheduled["deadline"] = deadline
            scheduled["callback"] = callback
            return _Handle()

    class _DeadlineClient(ClientCommandsMixin):
        def __init__(self) -> None:
            self.config = _config()
            self._motion_deadline_handle = None
            self._motion_deadline_generation = 0
            self.started: list[str] = []

        def _start_background_task(self, coro, name: str) -> None:
            coro.close()
            self.started.append(name)

    client = _DeadlineClient()
    monkeypatch.setattr("botparty_robot.client_commands.asyncio.get_running_loop", lambda: _Loop())
    client._arm_motion_deadline()
    assert scheduled["deadline"] == 12.0
    callback = scheduled["callback"]
    assert callable(callback)
    callback()
    assert client.started == ["motion_deadline"]

    adapter = _RaceAdapter(_config())
    assert adapter.is_motion_command(" F ") is True
    adapter.options["motion_commands"] = ["spin"]
    assert adapter.is_motion_command("SPIN") is True


def test_capability_manifest_is_deterministic_and_changes_with_safety_config() -> None:
    class _NoTTS(BaseTTSProfile):
        profile_name = "none"

    config = _config()
    adapter = _RaceAdapter(config)
    tts = _NoTTS(config)
    first = build_capability_manifest(config, adapter, [], tts)
    second = build_capability_manifest(config, adapter, [], tts)
    assert first == second
    assert first["hardware"]["adapter"] == "race"
    assert first["hardware"]["safeStop"] is True
    assert len(first["hash"]) == 64

    changed_config = _config()
    changed_config.safety.max_run_time_ms = 1_000
    changed = build_capability_manifest(
        changed_config,
        _RaceAdapter(changed_config),
        [],
        _NoTTS(changed_config),
    )
    assert changed["hash"] != first["hash"]
