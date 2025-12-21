
import asyncio
import logging
from pathlib import Path
import sys

# Add project root to path
sys.path.append(str(Path.cwd()))

from src.database_sqlite import SQLiteDatabase

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def test_duplication():
    # Use a test database file
    test_db_path = Path("test_duplication.db")
    if test_db_path.exists():
        test_db_path.unlink()
        
    db = SQLiteDatabase(db_path=test_db_path)
    
    target_id = "123456789"
    
    logger.info("--- Test 1: Check non-existent tweet ---")
    exists = await db.check_target_tweet_exists(target_id)
    logger.info(f"Exists (should be False): {exists}")
    assert not exists, "Tweet should not exist yet"
    
    logger.info("\n--- Test 2: Insert first time ---")
    id1 = await db.add_to_queue(
        target_tweet_id=target_id,
        target_author="test_user",
        target_content="Hello world",
        reply_text="Hi there",
    )
    logger.info(f"Inserted ID: {id1}")
    
    logger.info("\n--- Test 3: Check existing tweet ---")
    exists = await db.check_target_tweet_exists(target_id)
    logger.info(f"Exists (should be True): {exists}")
    assert exists, "Tweet should exist now"
    
    logger.info("\n--- Test 4: Insert duplicate ---")
    id2 = await db.add_to_queue(
        target_tweet_id=target_id,
        target_author="test_user",
        target_content="Hello world",
        reply_text="Different reply",
    )
    logger.info(f"Returned ID: {id2}")
    
    assert id1 == id2, "Should return existing ID for duplicate"
    logger.info("SUCCESS: Duplicate insertion returned existing ID")
    
    # Clean up
    if test_db_path.exists():
        test_db_path.unlink()

if __name__ == "__main__":
    asyncio.run(test_duplication())
