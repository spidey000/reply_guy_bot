
import sys
import logging
import asyncio
import os
import requests
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.ai_client import AIClient
from config import settings

# Load env vars
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger("DEBUG_AI")

async def test_fallback_chain():
    print("\n🧪 Testing AI Fallback Chain\n")
    
    # Manually configure fallbacks to test the logic
    # We'll use a fake model first to force a fallback
    primary_model = "fake-model-that-will-fail"
    fallback_models = ["z-ai/glm-4.5-air:free", "google/gemini-2.0-flash-exp:free"]
    
    print(f"Primary: {primary_model}")
    print(f"Fallbacks: {fallback_models}")
    
    client = AIClient(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=primary_model,
        fallback_models=fallback_models
    )
    
    print("\nAttempting generation (expecting fail + recovery)...")
    reply = await client.generate_reply(
        tweet_author="test_user",
        tweet_content="Testing fallback logic",
        max_tokens=20
    )
    
    if reply:
        print(f"\n✅ Success! Generated reply: {reply}")
    else:
        print("\n❌ Failed to generate reply even with fallbacks")

    # Now verify the actual configuration
    print("\n--- Verifying Actual Configuration ---")
    print(f"Configured Model: {settings.ai_model}")
    print(f"Configured Fallbacks: {settings.ai_fallback_models}")

if __name__ == "__main__":
    asyncio.run(test_fallback_chain())
