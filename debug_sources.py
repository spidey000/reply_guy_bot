import asyncio
import logging
from src.database import Database

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    print("--- Database Source Debugger ---")
    try:
        db = Database()
        # Force connection
        await db._ensure_connection()
        print("Connected to Supabase.")

        # 1. Target Accounts
        targets = await db.get_target_accounts()
        print(f"\n[Target Accounts] ({len(targets)}):")
        for t in targets:
            print(f" - {t}")

        # 2. Search Queries (ALL)
        print(f"\n[All Search Queries]:")
        res = db.client.table("search_queries").select("*").execute()
        for s in res.data:
            status = "✅" if s['enabled'] else "❌"
            print(f" {status} '{s['query']}'")

        # 3. Home Feed (ALL Settings)
        print(f"\n[Source Settings]:")
        res = db.client.table("source_settings").select("*").execute()
        for s in res.data:
             print(f" - {s['source_type']}: Enabled={s['enabled']}")

        # 4. Topics
        topics = await db.get_topics()
        print(f"\n[Topics] ({len(topics)}):")
        for t in topics:
            print(f" - {t}")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
