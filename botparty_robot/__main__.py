"""Entry point for the BotParty robot client."""

import asyncio
import argparse
import logging
import os
import secrets
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from .client import BotPartyClient, should_emit_runtime_log
from .config import RobotConfig, normalize_cameras
from . import __version__

def _resolve_log_level() -> int:
    configured = os.environ.get("BOTPARTY_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, configured, logging.INFO)


logging.basicConfig(
    level=_resolve_log_level(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("botparty")
SHUTDOWN_TIMEOUT_SECONDS = 15.0
LEGACY_CONFIG_DEPRECATION_DEADLINE = "2026-09-01"


class PlannedReconnectNoiseFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return should_emit_runtime_log(record)


for handler in logging.getLogger().handlers:
    handler.addFilter(PlannedReconnectNoiseFilter())


def _warn_legacy_config(section: str, target: str) -> None:
    logger.warning(
        "Deprecated config: top-level '%s' section detected. Migrate to '%s' in config.yaml. "
        "Legacy support will be removed after %s. See config.example.yaml.",
        section,
        target,
        LEGACY_CONFIG_DEPRECATION_DEADLINE,
    )


def _apply_legacy_hardware_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    if "hardware" in raw:
        return raw

    controls = raw.get("controls") or {}
    if not isinstance(controls, dict):
        return raw

    if controls.get("gpio_enabled"):
        _warn_legacy_config("controls", "hardware")
        raw["hardware"] = {
            "type": "l298n",
            "options": {
                "forward_pins": [controls.get("motor_left_forward")],
                "backward_pins": [controls.get("motor_left_backward")],
                "left_pins": [controls.get("motor_right_forward")],
                "right_pins": [controls.get("motor_right_backward")],
            },
        }
    else:
        raw["hardware"] = {"type": "none", "options": {}}

    return raw


def _apply_legacy_video_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    if "video" in raw:
        return raw

    camera = raw.get("camera") or {}
    if not isinstance(camera, dict):
        _warn_legacy_config("camera", "video")
        raw["video"] = {"type": "opencv", "options": {}}
        return raw

    _warn_legacy_config("camera", "video")
    pipeline = str(camera.get("pipeline", "opencv")).strip().lower()
    mapping = {
        "opencv": "opencv",
        "ffmpeg": "ffmpeg",
        "libcamera": "ffmpeg_libcamera",
        "ffmpeg-libcamera": "ffmpeg_libcamera",
    }
    raw["video"] = {"type": mapping.get(pipeline, pipeline), "options": {}}
    return raw


def _apply_legacy_tts_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    tts = raw.get("tts")
    if tts is None:
        raw["tts"] = {
            "enabled": False,
            "type": "none",
            "playback_device": "default",
            "volume": 70,
            "filter_urls": False,
            "allow_anonymous": True,
            "blocked_senders": [],
            "delay_ms": 0,
            "options": {},
        }
        return raw

    if not isinstance(tts, dict):
        raw["tts"] = {
            "enabled": False,
            "type": "none",
            "playback_device": "default",
            "volume": 70,
            "filter_urls": False,
            "allow_anonymous": True,
            "blocked_senders": [],
            "delay_ms": 0,
            "options": {},
        }
        return raw

    speaker_device = tts.get("playback_device")
    if not speaker_device:
        speaker_device = tts.get("speaker_device") or tts.get("audio_device")
    if not speaker_device:
        speaker_num = tts.get("speaker_num") or tts.get("hw_num")
        if isinstance(speaker_num, str) and speaker_num.strip():
            speaker_device = f"plughw:{speaker_num.strip()}"

    delay_ms = tts.get("delay_ms", 0)
    if not delay_ms and tts.get("delay_tts"):
        delay_value = tts.get("delay", 0)
        if isinstance(delay_value, (int, float)):
            delay_ms = int(delay_value * 1000)

    tts["enabled"] = bool(tts.get("enabled", tts.get("type", "none") != "none"))
    tts["type"] = str(tts.get("type", "none"))
    tts["playback_device"] = str(speaker_device or "default")
    tts["volume"] = int(tts.get("volume", tts.get("tts_volume", 70)))
    tts["filter_urls"] = bool(tts.get("filter_urls", tts.get("filter_url_tts", False)))
    tts["allow_anonymous"] = bool(tts.get("allow_anonymous", tts.get("anon_tts", True)))
    tts["blocked_senders"] = list(tts.get("blocked_senders", []))
    tts["delay_ms"] = int(delay_ms or 0)
    tts["options"] = dict(tts.get("options", {}))
    raw["tts"] = tts
    return raw


def load_config() -> RobotConfig:
    return _load_config_from(None)


async def _shutdown_with_timeout(client: BotPartyClient, main_task: asyncio.Task[None]) -> None:
    try:
        await asyncio.wait_for(client.shutdown(), timeout=SHUTDOWN_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        logger.error(
            "Shutdown timed out after %.1fs; forcing event loop cancellation",
            SHUTDOWN_TIMEOUT_SECONDS,
        )
        main_task.cancel()


def _load_config_from(path_override: str | None) -> RobotConfig:
    env_path = os.environ.get("BOTPARTY_CONFIG")
    config_path = Path(path_override or env_path or "config.yaml")
    if not config_path.exists():
        logger.error("%s not found. Copy config.example.yaml and edit it.", config_path)
        sys.exit(1)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        logger.error("config.yaml must contain a YAML object at the top level")
        sys.exit(1)

    raw = _apply_legacy_hardware_defaults(raw)
    raw = _apply_legacy_video_defaults(raw)
    raw = _apply_legacy_tts_defaults(raw)
    server = raw.get("server")
    if isinstance(server, dict):
        device_key = server.get("device_key")
        if not isinstance(device_key, str) or not device_key.strip():
            device_key_path = Path(".botparty-device-key")
            try:
                stored_device_key = device_key_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                stored_device_key = ""
            except OSError:
                stored_device_key = ""

            if not stored_device_key:
                stored_device_key = secrets.token_hex(32)
                try:
                    device_key_path.write_text(f"{stored_device_key}\n", encoding="utf-8")
                    os.chmod(device_key_path, 0o600)
                except OSError:
                    logger.warning("Failed to persist .botparty-device-key; reconnects may require re-claim")

            server["device_key"] = stored_device_key
    return RobotConfig(**raw)


async def main() -> None:
    parser = argparse.ArgumentParser(description="BotParty Robot Client")
    parser.add_argument("--config", metavar="PATH", help="Path to config YAML (overrides BOTPARTY_CONFIG env var)")
    args = parser.parse_args()
    config = _load_config_from(args.config)

    if config.server.claim_token == "PASTE_YOUR_CLAIM_TOKEN_HERE":
        logger.error("Please set your claim_token in config.yaml!")
        sys.exit(1)

    logger.info("🤖 BotParty Robot Client v%s", __version__)
    logger.info(f"   API: {config.server.api_url}")
    logger.info(f"   LiveKit: {config.server.livekit_url}")
    logger.info(f"   Hardware: {config.hardware.type}")
    logger.info(f"   Video: {config.video.type}")
    logger.info(f"   TTS: {config.tts.type} (enabled={config.tts.enabled})")
    normalized_cameras = normalize_cameras(config)
    logger.info(f"   Cameras: {len(normalized_cameras)} configured")
    for camera in normalized_cameras:
        logger.info(
            "     - %s (%s) device=%s %dx%d@%dfps profile=%s",
            camera.label,
            camera.id,
            camera.camera.device,
            camera.camera.width,
            camera.camera.height,
            camera.camera.fps,
            camera.video.type,
        )

    client = BotPartyClient(config)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    main_task = asyncio.current_task()
    if main_task is None:
        raise RuntimeError("main task is not available")
    shutdown_task: asyncio.Task[None] | None = None

    def request_shutdown() -> None:
        nonlocal shutdown_task
        if shutdown_task is not None and not shutdown_task.done():
            return
        shutdown_task = asyncio.create_task(_shutdown_with_timeout(client, main_task))

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, request_shutdown)

    try:
        await client.run()
    except asyncio.CancelledError:
        if shutdown_task is not None and shutdown_task.done():
            return
        raise


if __name__ == "__main__":
    asyncio.run(main())
