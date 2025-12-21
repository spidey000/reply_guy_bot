
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from src.ai_client import AIClient
from config.settings import Settings

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

async def test_ai():
    settings = Settings()
    
    print(f"🤖 Initializing AI Client with model: {settings.ai_model}")
    client = AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        fallback_models=settings.ai_fallback_models
    )

    # Fake tweet
    tweet_author = "test_user"
    tweet_content = "I just realized that Python's asyncio is actually pretty cool once you understand the event loop! #coding #python"
    
    print(f"\n📨 Generating reply for tweet:\n'{tweet_content}'\n")

    # Call with high max_tokens to strict limits
    reply = await client.generate_reply(
        tweet_author=tweet_author,
        tweet_content=tweet_content,
        max_tokens=4000 # Use high limit as requested
    )

    print("\n✅ Response Received:")
    print("-" * 50)
    print(reply)
    print("-" * 50)
    print(f"Length: {len(reply) if reply else 0} chars")

if __name__ == "__main__":
    asyncio.run(test_ai())
