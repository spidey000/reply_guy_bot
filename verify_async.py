import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.getcwd())

from src.ai_client import AIClient
from config import settings

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger("verify_async")

async def keep_alive():
    """Prints a tick every 0.5s to prove loop is running."""
    try:
        while True:
            logger.info("Tick (Loop is running)")
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        pass

async def main():
    logger.info("Starting verification...")
    
    # Initialize client
    client = AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        fallback_models=settings.ai_fallback_models,
    )
    
    # Start keepalive
    keep_alive_task = asyncio.create_task(keep_alive())
    
    logger.info("Testing health check...")
    healthy = await client.health_check()
    logger.info(f"Health check result: {healthy}")
    
    logger.info("Testing generation (should happen in parallel with Ticks)...")
    reply = await client.generate_reply(
        tweet_author="test_user",
        tweet_content="Tell me a joke about asynchronous programming.",
        max_tokens=50
    )
    
    logger.info(f"Generated reply: {reply}")
    
    # Cleanup
    keep_alive_task.cancel()
    await keep_alive_task
    logger.info("Verification complete.")

if __name__ == "__main__":
    asyncio.run(main())
