import importlib
import pkgutil

import pytest

from botparty_robot.hardware.base import BaseHardware
from botparty_robot.profile_options import (
    HARDWARE_OPTION_MODELS,
    TTS_OPTION_MODELS,
    VIDEO_OPTION_MODELS,
)
from botparty_robot.tts.base import BaseTTSProfile
from botparty_robot.video.base import BaseVideoProfile

PROFILE_PACKAGES = (
    "botparty_robot.hardware",
    "botparty_robot.tts",
    "botparty_robot.video",
)


def _profile_modules() -> list[str]:
    modules: list[str] = []
    for package_name in PROFILE_PACKAGES:
        package = importlib.import_module(package_name)
        modules.extend(
            info.name for info in pkgutil.iter_modules(package.__path__, prefix=f"{package_name}.")
        )
    return sorted(modules)


@pytest.mark.parametrize("module_name", _profile_modules())
def test_every_profile_module_is_importable_without_optional_sdks(module_name: str) -> None:
    """Optional device SDKs must be resolved during setup, never at module import."""

    assert importlib.import_module(module_name).__name__ == module_name


@pytest.mark.parametrize(
    ("package", "profile", "class_name", "base_class"),
    [
        *(
            ("hardware", profile, "HardwareAdapter", BaseHardware)
            for profile in HARDWARE_OPTION_MODELS
            if profile != "auto"
        ),
        ("hardware", "custom", "HardwareAdapter", BaseHardware),
        *(("video", profile, "VideoProfile", BaseVideoProfile) for profile in VIDEO_OPTION_MODELS),
        *(("tts", profile, "TTSProfile", BaseTTSProfile) for profile in TTS_OPTION_MODELS),
        ("tts", "custom", "TTSProfile", BaseTTSProfile),
    ],
)
def test_every_registered_profile_exposes_the_expected_contract(
    package: str,
    profile: str,
    class_name: str,
    base_class: type[object],
) -> None:
    module = importlib.import_module(f"botparty_robot.{package}.{profile}")
    implementation = getattr(module, class_name)

    assert issubclass(implementation, base_class)
    assert implementation.profile_name == profile
