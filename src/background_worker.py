"""
Background Worker - Async publication loop.

This module runs continuously in the background, checking for tweets
that are ready to be published and executing them via Ghost Delegate.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                   BACKGROUND WORKER                         │
    ├─────────────────────────────────────────────────────────────┤
    │  Loop (every N seconds):                                    │
    │  1. Query database for tweets where scheduled_at <= now    │
    │  2. For each pending tweet:                                │
    │     a. Call Ghost Delegate to publish                      │
    │     b. Update database with result                         │
    │     c. Notify via Telegram (optional)                      │
    │  3. Sleep until next check                                 │
    └─────────────────────────────────────────────────────────────┘

Integration:
    This worker runs as an asyncio task alongside the main bot loop.
    It's independent of the tweet detection and approval flow.

Configuration:
    SCHEDULER_CHECK_INTERVAL: Seconds between queue checks (default: 60)
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from config import settings
from src.alerts import get_alerts

if TYPE_CHECKING:
    from src.database import Database
    from src.telegram_client import TelegramClient
    from src.x_delegate import GhostDelegate

logger = logging.getLogger(__name__)


async def run_worker(
    db: "Database",
    ghost_delegate: "GhostDelegate",
    telegram: "TelegramClient | None" = None,
    check_interval: int | None = None,
) -> None:
    """
    Main worker loop that processes scheduled tweets.

    This function runs indefinitely, checking for and publishing
    tweets at their scheduled times.

    Args:
        db: Database instance for querying pending tweets.
        ghost_delegate: GhostDelegate instance for publishing.
        telegram: Optional Telegram client for notifications.
        check_interval: Seconds between checks. Defaults to config value.
    """
    interval = check_interval or settings.scheduler_check_interval
    logger.info(f"Background worker started (interval: {interval}s)")

    while True:
        try:
            await process_pending_tweets(db, ghost_delegate, telegram)
        except Exception as e:
            logger.error(f"Error in background worker: {e}")

        await asyncio.sleep(interval)


async def process_pending_tweets(
    db: "Database",
    ghost_delegate: "GhostDelegate",
    telegram: "TelegramClient | None" = None,
) -> int:
    """
    Process all tweets ready for publication.

    Queries the database for tweets where scheduled_at <= now and
    posts them using Ghost Delegate.

    Args:
        db: Database instance.
        ghost_delegate: GhostDelegate instance.
        telegram: Optional Telegram client.

    Returns:
        Number of tweets processed.
    """
    now = datetime.now()

    # Get pending tweets from database
    pending = await db.get_pending_tweets(before=now)

    if not pending:
        return 0

    logger.info(f"Processing {len(pending)} pending tweet(s)")
    processed = 0

    for tweet in pending:
        success = await _publish_tweet(tweet, ghost_delegate, db, telegram)

        if success:
            processed += 1
            if telegram:
                await _notify_published(tweet, telegram)

    return processed


async def _publish_tweet(
    tweet: dict,
    ghost_delegate: "GhostDelegate",
    db: "Database",
    telegram: "TelegramClient | None" = None,
) -> bool:
    """
    Publish a single tweet and update database.

    Args:
        tweet: Tweet data dictionary.
        ghost_delegate: GhostDelegate instance.
        db: Database instance.

    Returns:
        True if published successfully, False otherwise.
    """
    tweet_id = tweet.get("id")
    target_tweet_id = tweet.get("target_tweet_id")
    reply_text = tweet.get("reply_text")

    try:
        success = await ghost_delegate.post_as_main(target_tweet_id, reply_text)

        if success:
            await db.mark_as_posted(tweet_id)
            logger.info(f"Published tweet {tweet_id}")
        else:
            # Add to dead letter queue for retry (T017-S3)
            error_msg = "Publication failed"
            await db.mark_as_failed(tweet_id, error=error_msg)
            await db.add_to_dead_letter_queue(
                tweet_queue_id=tweet_id,
                target_tweet_id=target_tweet_id,
                error=error_msg,
                retry_count=0,
            )
            logger.error(f"Failed to publish tweet {tweet_id}, added to DLQ")

            # Notify via AlertManager
            alerts = get_alerts()
            if alerts:
                await alerts.error(
                    "publication_failed",
                    f"Failed to publish reply for tweet {target_tweet_id}",
                    tweet_id=tweet_id,
                    error=error_msg
                )

        return success

    except Exception as e:
        # Add to dead letter queue for retry (T017-S3)
        error_msg = str(e)
        await db.mark_as_failed(tweet_id, error=error_msg)
        try:
            await db.add_to_dead_letter_queue(
                tweet_queue_id=tweet_id,
                target_tweet_id=target_tweet_id,
                error=error_msg,
                retry_count=0,
            )
            logger.error(f"Error publishing tweet {tweet_id}, added to DLQ: {e}")
        except Exception as dlq_error:
            logger.error(f"Failed to add to DLQ: {dlq_error}")

        # Notify via AlertManager
        alerts = get_alerts()
        if alerts:
            await alerts.critical(
                "publication_error",
                f"Critical error publishing reply for tweet {target_tweet_id}",
                tweet_id=tweet_id,
                error=error_msg
            )

        return False


async def _notify_published(tweet: dict, telegram: "TelegramClient") -> None:
    """Send notification that a tweet was published."""
    try:
        await telegram.send_published_notification(tweet)
    except Exception as e:
        logger.error(f"Failed to send publish notification: {e}")


async def get_queue_status(db: "Database") -> dict:
    """
    Get current queue status for monitoring.

    Args:
        db: Database instance.

    Returns:
        Dictionary with queue statistics.
    """
    pending = await db.get_pending_count()
    today_posted = await db.get_posted_today_count()

    return {
        "pending": pending,
        "posted_today": today_posted,
        "next_check": datetime.now().isoformat(),
    }
