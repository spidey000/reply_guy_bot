"""
Main orchestrator for Reply Guy Bot.

This module coordinates all bot components and manages the main event loop.

Responsibilities:
    1. Initialize all components (AI, Telegram, Database, Ghost Delegate)
    2. Monitor target accounts for new tweets
    3. Generate AI replies for detected tweets
    4. Send approval requests via Telegram
    5. Schedule approved tweets via Burst Mode
    6. Publish tweets using Ghost Delegate

Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │  1. Monitor target accounts for new tweets                  │
    │  2. Generate AI reply                                       │
    │  3. Send to Telegram for approval                          │
    │  4. On approval → Calculate schedule time (Burst Mode)     │
    │  5. Background worker publishes at scheduled time          │
    │  6. Ghost Delegate handles secure publication              │
    └─────────────────────────────────────────────────────────────┘

Entry Point:
    python -m src.bot
"""

import asyncio
import logging

from config import settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """
    Main entry point for the bot.

    Initializes all components and starts the main event loop.
    """
    logger.info("Starting Reply Guy Bot v0.1.0")
    logger.info(f"Burst Mode: {'enabled' if settings.burst_mode_enabled else 'disabled'}")

    # TODO: Initialize components
    # - AIClient
    # - TelegramClient
    # - Database
    # - GhostDelegate
    # - BackgroundWorker

    # TODO: Start main monitoring loop

    logger.info("Bot initialized successfully")

    # Keep the bot running
    try:
        while True:
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        logger.info("Bot shutdown requested")


if __name__ == "__main__":
    asyncio.run(main())
