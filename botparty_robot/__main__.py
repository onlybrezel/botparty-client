"""Entry point for the BotParty robot client."""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

from .client import BotPartyClient
from .config import RobotConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("botparty")


def load_config() -> RobotConfig:
    config_path = Path("config.yaml")
    if not config_path.exists():
        logger.error("config.yaml not found. Copy config.example.yaml to config.yaml and edit it.")
        sys.exit(1)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    return RobotConfig(**raw)


async def main() -> None:
    config = load_config()

    if config.server.claim_token == "PASTE_YOUR_CLAIM_TOKEN_HERE":
        logger.error("Please set your claim_token in config.yaml!")
        sys.exit(1)

    logger.info("🤖 BotParty Robot Client v0.1.0")
    logger.info(f"   API: {config.server.api_url}")
    logger.info(f"   LiveKit: {config.server.livekit_url}")

    client = BotPartyClient(config)

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(client.shutdown()))

    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
