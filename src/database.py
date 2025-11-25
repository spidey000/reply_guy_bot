"""
Database Client - Supabase integration for persistence.

This module handles all database operations using Supabase as the backend.

Tables:
    tweet_queue:
        - id: UUID (primary key)
        - target_tweet_id: Tweet ID to reply to
        - target_author: Author of the original tweet
        - target_content: Content of the original tweet
        - reply_text: Generated reply text
        - status: pending | approved | posted | failed
        - scheduled_at: When to post (Burst Mode)
        - posted_at: When actually posted
        - created_at: Record creation time
        - error: Error message if failed

    target_accounts:
        - id: UUID (primary key)
        - handle: Twitter handle to monitor
        - enabled: Whether currently monitoring
        - created_at: Record creation time

SQL Setup (run in Supabase SQL Editor):
    -- Tweet queue table
    CREATE TABLE tweet_queue (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        target_tweet_id TEXT NOT NULL,
        target_author TEXT NOT NULL,
        target_content TEXT,
        reply_text TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        scheduled_at TIMESTAMP,
        posted_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT NOW(),
        error TEXT
    );

    -- Target accounts table
    CREATE TABLE target_accounts (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        handle TEXT NOT NULL UNIQUE,
        enabled BOOLEAN DEFAULT true,
        created_at TIMESTAMP DEFAULT NOW()
    );
"""

import logging
from datetime import datetime
from typing import Optional

from supabase import create_client, Client

from config import settings

logger = logging.getLogger(__name__)


class Database:
    """
    Supabase client for tweet queue and account management.

    Provides async-friendly methods for all database operations
    required by the Reply Guy Bot.
    """

    def __init__(
        self,
        url: str | None = None,
        key: str | None = None,
    ) -> None:
        """
        Initialize database connection.

        Args:
            url: Supabase project URL. Defaults to config.
            key: Supabase anon/service key. Defaults to config.
        """
        self.client: Client = create_client(
            url or settings.supabase_url,
            key or settings.supabase_key,
        )
        logger.info("Database client initialized")

    # =========================================================================
    # Tweet Queue Operations
    # =========================================================================

    async def add_to_queue(
        self,
        target_tweet_id: str,
        target_author: str,
        target_content: str,
        reply_text: str,
    ) -> str:
        """
        Add a new tweet to the queue.

        Args:
            target_tweet_id: ID of tweet to reply to.
            target_author: Author of original tweet.
            target_content: Content of original tweet.
            reply_text: Generated reply text.

        Returns:
            ID of the created queue entry.
        """
        result = self.client.table("tweet_queue").insert({
            "target_tweet_id": target_tweet_id,
            "target_author": target_author,
            "target_content": target_content,
            "reply_text": reply_text,
            "status": "pending",
        }).execute()

        tweet_id = result.data[0]["id"]
        logger.info(f"Added tweet to queue: {tweet_id}")
        return tweet_id

    async def approve_tweet(
        self,
        tweet_id: str,
        scheduled_at: datetime,
    ) -> None:
        """
        Approve a tweet and schedule it for posting.

        Args:
            tweet_id: Queue entry ID.
            scheduled_at: When to post the tweet.
        """
        self.client.table("tweet_queue").update({
            "status": "approved",
            "scheduled_at": scheduled_at.isoformat(),
        }).eq("id", tweet_id).execute()

        logger.info(f"Approved tweet {tweet_id} for {scheduled_at}")

    async def reject_tweet(self, tweet_id: str) -> None:
        """
        Reject and remove a tweet from queue.

        Args:
            tweet_id: Queue entry ID.
        """
        self.client.table("tweet_queue").update({
            "status": "rejected",
        }).eq("id", tweet_id).execute()

        logger.info(f"Rejected tweet {tweet_id}")

    async def get_pending_tweets(
        self,
        before: datetime | None = None,
    ) -> list[dict]:
        """
        Get tweets ready for posting.

        Args:
            before: Only get tweets scheduled before this time.

        Returns:
            List of tweet dictionaries.
        """
        query = self.client.table("tweet_queue").select("*").eq(
            "status", "approved"
        ).is_("posted_at", "null")

        if before:
            query = query.lte("scheduled_at", before.isoformat())

        result = query.order("scheduled_at").execute()
        return result.data

    async def mark_as_posted(self, tweet_id: str) -> None:
        """
        Mark a tweet as successfully posted.

        Args:
            tweet_id: Queue entry ID.
        """
        self.client.table("tweet_queue").update({
            "status": "posted",
            "posted_at": datetime.now().isoformat(),
        }).eq("id", tweet_id).execute()

        logger.info(f"Marked tweet {tweet_id} as posted")

    async def mark_as_failed(
        self,
        tweet_id: str,
        error: str,
    ) -> None:
        """
        Mark a tweet as failed.

        Args:
            tweet_id: Queue entry ID.
            error: Error message.
        """
        self.client.table("tweet_queue").update({
            "status": "failed",
            "error": error,
        }).eq("id", tweet_id).execute()

        logger.error(f"Marked tweet {tweet_id} as failed: {error}")

    async def get_pending_count(self) -> int:
        """Get count of pending tweets in queue."""
        result = self.client.table("tweet_queue").select(
            "id", count="exact"
        ).eq("status", "approved").is_("posted_at", "null").execute()

        return result.count or 0

    async def get_posted_today_count(self) -> int:
        """Get count of tweets posted today."""
        today = datetime.now().replace(hour=0, minute=0, second=0)

        result = self.client.table("tweet_queue").select(
            "id", count="exact"
        ).eq("status", "posted").gte(
            "posted_at", today.isoformat()
        ).execute()

        return result.count or 0

    # =========================================================================
    # Target Accounts Operations
    # =========================================================================

    async def get_target_accounts(self) -> list[str]:
        """
        Get list of accounts to monitor.

        Returns:
            List of Twitter handles.
        """
        result = self.client.table("target_accounts").select(
            "handle"
        ).eq("enabled", True).execute()

        return [row["handle"] for row in result.data]

    async def add_target_account(self, handle: str) -> None:
        """
        Add a new account to monitor.

        Args:
            handle: Twitter handle (without @).
        """
        self.client.table("target_accounts").upsert({
            "handle": handle.lower().replace("@", ""),
            "enabled": True,
        }).execute()

        logger.info(f"Added target account: @{handle}")

    async def remove_target_account(self, handle: str) -> None:
        """
        Remove an account from monitoring.

        Args:
            handle: Twitter handle.
        """
        self.client.table("target_accounts").update({
            "enabled": False,
        }).eq("handle", handle.lower().replace("@", "")).execute()

        logger.info(f"Removed target account: @{handle}")
