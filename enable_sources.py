import asyncio
import logging
from src.database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    print("--- Enabling Sources ---")
    try:
        db = Database()
        await db._ensure_connection()

        # Enable Home Feed
        print("Enabling 'home_feed_following'...")
        await db.set_source_enabled("home_feed_following", True)
        
        # Verify
        s = await db.get_source_settings("home_feed_following")
        print(f"Result: {s}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
