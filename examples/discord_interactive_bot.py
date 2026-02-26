"""Example script to test the Discord interactive bot plugin initialization."""

import asyncio
import logging
import os

from nexus.adapters.registry import AdapterRegistry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    bot_token = os.environ.get("DISCORD_BOT_TOKEN", "fake.discord.token.here")

    registry = AdapterRegistry()
    config = {
        "interactive_clients": [
            {
                "type": "discord-interactive-http",
                "config": {"bot_token": bot_token, "command_prefix": "!"},
            }
        ]
    }

    try:
        adapters = registry.from_config(config)
        client = adapters.interactive_clients[0]

        logger.info(f"Loaded plugin: {client.name}")
        logger.info("Calling start() to test initialization...")

        # Start the client but we will instantly stop it just to see if it boots
        asyncio.create_task(client.start())

        # Give it a second to fail login with fake token
        await asyncio.sleep(2)

        logger.info("Calling stop()...")
        await client.stop()

    except Exception as e:
        logger.error(f"Error: {e}")


if __name__ == "__main__":
    asyncio.run(main())
