"""Entry point for the BotParty robot client."""

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from .client import BotPartyClient
from .config import RobotConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("botparty")


def _apply_legacy_hardware_defaults(raw: dict[str, Any]) -> dict[str, Any]:
    if "hardware" in raw:
        return raw

    controls = raw.get("controls") or {}
    if not isinstance(controls, dict):
        return raw

    if controls.get("gpio_enabled"):
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
        raw["video"] = {"type": "opencv", "options": {}}
        return raw

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
    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.error("config.yaml not found. Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        logger.error("config.yaml must contain a YAML object at the top level")
        sys.exit(1)

    raw = _apply_legacy_hardware_defaults(raw)
    raw = _apply_legacy_video_defaults(raw)
    raw = _apply_legacy_tts_defaults(raw)
    return RobotConfig(**raw)


async def main() -> None:
    config = load_config()

    if config.server.claim_token == "PASTE_YOUR_CLAIM_TOKEN_HERE":
        logger.error("Please set your claim_token in config.yaml!")
        sys.exit(1)

    logger.info("🤖 BotParty Robot Client v0.1.0")
    logger.info(f"   API: {config.server.api_url}")
    logger.info(f"   LiveKit: {config.server.livekit_url}")
    logger.info(f"   Hardware: {config.hardware.type}")
    logger.info(f"   Video: {config.video.type}")
    logger.info(f"   TTS: {config.tts.type} (enabled={config.tts.enabled})")

    client = BotPartyClient(config)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(client.shutdown()))

    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
