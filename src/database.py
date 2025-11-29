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

    failed_tweets (Dead Letter Queue):
        - id: UUID (primary key)
        - tweet_queue_id: UUID reference to original tweet
        - target_tweet_id: Tweet ID to reply to
        - error: Error message
        - retry_count: Number of retry attempts
        - created_at: When added to DLQ
        - last_retry_at: Last retry attempt
        - status: pending | retrying | exhausted

    login_history (Ban Prevention):
        - id: UUID (primary key)
        - account_type: 'dummy' or 'main'
        - login_type: 'fresh' (credentials) or 'cookie_restore' (session)
        - success: Whether login succeeded
        - error_message: Error details if failed
        - error_type: Exception class name
        - attempted_at: Timestamp of attempt
        - cookies_existed: Did cookies exist?
        - cookies_valid: Were cookies valid?

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

    -- Failed tweets (Dead Letter Queue)
    CREATE TABLE failed_tweets (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        tweet_queue_id UUID REFERENCES tweet_queue(id),
        target_tweet_id TEXT NOT NULL,
        error TEXT,
        retry_count INT DEFAULT 0,
        created_at TIMESTAMP DEFAULT NOW(),
        last_retry_at TIMESTAMP,
        status TEXT DEFAULT 'pending'
    );

    -- Login history (Ban Prevention)
    CREATE TABLE login_history (
        id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
        account_type TEXT NOT NULL CHECK (account_type IN ('dummy', 'main')),
        login_type TEXT NOT NULL CHECK (login_type IN ('fresh', 'cookie_restore')),
        success BOOLEAN NOT NULL,
        error_message TEXT,
        error_type TEXT,
        attempted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        cookies_existed BOOLEAN DEFAULT false,
        cookies_valid BOOLEAN
    );

    -- Index for finding last successful fresh login
    CREATE INDEX IF NOT EXISTS idx_login_history_fresh_success
        ON login_history(attempted_at DESC)
        WHERE login_type = 'fresh' AND success = true;
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions

from config import settings
from src.circuit_breaker import CircuitBreaker, with_backoff

logger = logging.getLogger(__name__)


class Database:
    """
    Supabase client for tweet queue and account management.

    Provides async-friendly methods for all database operations
    required by the Reply Guy Bot.

    Features:
    - Automatic connection recovery
    - Dead letter queue for failed operations
    - Circuit breaker protection
    - Health monitoring
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
        self._url = url or settings.supabase_url
        self._key = key or settings.supabase_key
        self.client: Optional[Client] = None
        self._is_connected = False

        # Circuit breaker for database operations
        self.circuit_breaker = CircuitBreaker(
            name="database",
            failure_threshold=3,
            recovery_timeout=30,
            half_open_max_calls=2,
        )

        # Initialize connection
        self._connect()
        logger.info("Database client initialized")

    def _connect(self) -> None:
        """Establish database connection."""
        try:
            self.client = create_client(
                self._url,
                self._key,
            )
            self._is_connected = True
            logger.info("Database connection established")
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            self._is_connected = False
            raise

    @with_backoff(max_retries=3, base_delay=1, max_delay=10)
    async def _ensure_connection(self) -> None:
        """
        Ensure database connection is active, reconnect if needed.

        Raises:
            Exception: If reconnection fails after retries.
        """
        if not self._is_connected or self.client is None:
            logger.warning("Database connection lost, attempting reconnect...")
            self._connect()

    async def health_check(self) -> bool:
        """
        Check database connection health.

        Returns:
            True if database is accessible, False otherwise.
        """
        try:
            await self._ensure_connection()
            # Simple query to verify connection
            result = self.client.table("tweet_queue").select("id", count="exact").limit(1).execute()
            logger.debug("Database health check: OK")
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            self._is_connected = False
            return False

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
        await self._ensure_connection()

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
        await self._ensure_connection()

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
        await self._ensure_connection()

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
        await self._ensure_connection()

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
        await self._ensure_connection()

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
        await self._ensure_connection()

        self.client.table("tweet_queue").update({
            "status": "failed",
            "error": error,
        }).eq("id", tweet_id).execute()

        logger.error(f"Marked tweet {tweet_id} as failed: {error}")

    async def get_pending_count(self) -> int:
        """Get count of pending tweets in queue."""
        await self._ensure_connection()

        result = self.client.table("tweet_queue").select(
            "id", count="exact"
        ).eq("status", "approved").is_("posted_at", "null").execute()

        return result.count or 0

    async def get_posted_today_count(self) -> int:
        """Get count of tweets posted today."""
        await self._ensure_connection()

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
        await self._ensure_connection()

        result = self.client.table("target_accounts").select(
            "handle"
        ).eq("enabled", True).execute()

        return [row["handle"] for row in result.data]

    async def add_target_account(self, handle: str) -> str:
        """
        Add a new account to monitor.

        Args:
            handle: Twitter handle (without @).

        Returns:
            Status: 'added', 're-enabled', or 'already_active'
        """
        await self._ensure_connection()
        handle = handle.lower().replace("@", "")

        # Check if handle already exists
        existing = self.client.table("target_accounts").select(
            "enabled"
        ).eq("handle", handle).execute()

        if existing.data:
            if existing.data[0]["enabled"]:
                return "already_active"
            else:
                # Re-enable disabled target
                self.client.table("target_accounts").update({
                    "enabled": True,
                }).eq("handle", handle).execute()
                logger.info(f"Re-enabled target account: @{handle}")
                return "re-enabled"
        else:
            # Insert new target
            self.client.table("target_accounts").insert({
                "handle": handle,
                "enabled": True,
            }).execute()
            logger.info(f"Added target account: @{handle}")
            return "added"

    async def remove_target_account(self, handle: str) -> None:
        """
        Remove an account from monitoring.

        Args:
            handle: Twitter handle.
        """
        await self._ensure_connection()

        self.client.table("target_accounts").update({
            "enabled": False,
        }).eq("handle", handle.lower().replace("@", "")).execute()

        logger.info(f"Removed target account: @{handle}")

    # =========================================================================
    # Dead Letter Queue Operations (T017-S3)
    # =========================================================================

    async def add_to_dead_letter_queue(
        self,
        tweet_queue_id: str,
        target_tweet_id: str,
        error: str,
        retry_count: int = 0,
    ) -> str:
        """
        Add failed tweet to dead letter queue for retry.

        Args:
            tweet_queue_id: UUID of the original tweet in queue.
            target_tweet_id: Tweet ID to reply to.
            error: Error message describing the failure.
            retry_count: Current retry attempt count.

        Returns:
            ID of the dead letter queue entry.
        """
        try:
            await self._ensure_connection()

            result = self.client.table("failed_tweets").insert({
                "tweet_queue_id": tweet_queue_id,
                "target_tweet_id": target_tweet_id,
                "error": error,
                "retry_count": retry_count,
                "status": "pending",
            }).execute()

            dlq_id = result.data[0]["id"]
            logger.info(f"Added to dead letter queue: {dlq_id} (retry_count={retry_count})")
            return dlq_id

        except Exception as e:
            logger.error(f"Failed to add to dead letter queue: {e}")
            raise

    async def get_dead_letter_items(
        self,
        max_items: int = 10,
        max_retry_count: int = 5,
    ) -> list[dict]:
        """
        Get items from dead letter queue ready for retry.

        Args:
            max_items: Maximum number of items to retrieve.
            max_retry_count: Skip items that exceeded this retry count.

        Returns:
            List of failed tweet dictionaries.
        """
        try:
            await self._ensure_connection()

            result = self.client.table("failed_tweets").select(
                "*"
            ).eq(
                "status", "pending"
            ).lt(
                "retry_count", max_retry_count
            ).order(
                "created_at"
            ).limit(max_items).execute()

            return result.data

        except Exception as e:
            logger.error(f"Failed to get dead letter items: {e}")
            return []

    async def retry_dead_letter_item(
        self,
        item_id: str,
        success: bool,
        error: Optional[str] = None,
    ) -> None:
        """
        Update dead letter queue item after retry attempt.

        Args:
            item_id: Dead letter queue item ID.
            success: Whether retry was successful.
            error: New error message if retry failed.
        """
        try:
            await self._ensure_connection()

            if success:
                # Mark as successfully processed
                self.client.table("failed_tweets").update({
                    "status": "retried_successfully",
                    "last_retry_at": datetime.now().isoformat(),
                }).eq("id", item_id).execute()

                logger.info(f"Dead letter item {item_id} successfully retried")

            else:
                # Increment retry count
                item = self.client.table("failed_tweets").select("retry_count").eq("id", item_id).execute()

                if item.data:
                    new_count = item.data[0]["retry_count"] + 1

                    # Check if exhausted
                    status = "exhausted" if new_count >= 5 else "pending"

                    self.client.table("failed_tweets").update({
                        "retry_count": new_count,
                        "last_retry_at": datetime.now().isoformat(),
                        "error": error or "Retry failed",
                        "status": status,
                    }).eq("id", item_id).execute()

                    if status == "exhausted":
                        logger.error(f"Dead letter item {item_id} exhausted after {new_count} retries")
                    else:
                        logger.warning(f"Dead letter item {item_id} retry failed (attempt {new_count})")

        except Exception as e:
            logger.error(f"Failed to update dead letter item {item_id}: {e}")

    async def get_dead_letter_stats(self) -> dict:
        """
        Get statistics about dead letter queue.

        Returns:
            Dictionary with DLQ statistics.
        """
        try:
            await self._ensure_connection()

            pending = self.client.table("failed_tweets").select(
                "id", count="exact"
            ).eq("status", "pending").execute()

            exhausted = self.client.table("failed_tweets").select(
                "id", count="exact"
            ).eq("status", "exhausted").execute()

            return {
                "pending": pending.count or 0,
                "exhausted": exhausted.count or 0,
                "total": (pending.count or 0) + (exhausted.count or 0),
            }

        except Exception as e:
            logger.error(f"Failed to get dead letter stats: {e}")
            return {"pending": 0, "exhausted": 0, "total": 0}

    # =========================================================================
    # Crash Recovery (T017-S5)
    # =========================================================================

    async def recover_stale_tweets(self, timeout_minutes: int = 30) -> int:
        """
        Recover tweets that were being processed but crashed/stalled.

        This marks tweets that have been "in_progress" for too long as "pending"
        so they can be retried.

        Args:
            timeout_minutes: Minutes before considering a tweet stale.

        Returns:
            Number of tweets recovered.
        """
        try:
            await self._ensure_connection()

            # Calculate cutoff time
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(minutes=timeout_minutes)

            # Note: This assumes we add an "in_progress" status and "processing_started_at" field
            # For MVP, we'll focus on recovering failed tweets instead
            result = self.client.table("tweet_queue").update({
                "status": "approved",  # Back to approved for retry
            }).eq(
                "status", "failed"
            ).is_(
                "posted_at", "null"
            ).execute()

            recovered = len(result.data) if result.data else 0

            if recovered > 0:
                logger.info(f"Recovered {recovered} stale/failed tweets")

            return recovered

        except Exception as e:
            logger.error(f"Failed to recover stale tweets: {e}")
            return 0

    # =========================================================================
    # Login Tracking (Ban Prevention)
    # =========================================================================

    async def record_login_attempt(
        self,
        account_type: str,
        login_type: str,
        success: bool,
        error_message: Optional[str] = None,
        error_type: Optional[str] = None,
        cookies_existed: bool = False,
        cookies_valid: Optional[bool] = None,
    ) -> str:
        """
        Record a login attempt for tracking and cooldown enforcement.

        Args:
            account_type: 'dummy' or 'main'
            login_type: 'fresh' (credentials) or 'cookie_restore' (session)
            success: Whether login succeeded
            error_message: Error details if failed
            error_type: Exception class name for categorization
            cookies_existed: Did cookies.json exist at attempt time?
            cookies_valid: Were cookies valid (null if fresh login)?

        Returns:
            ID of the login history entry.
        """
        try:
            await self._ensure_connection()

            result = self.client.table("login_history").insert({
                "account_type": account_type,
                "login_type": login_type,
                "success": success,
                "error_message": error_message,
                "error_type": error_type,
                "cookies_existed": cookies_existed,
                "cookies_valid": cookies_valid,
            }).execute()

            login_id = result.data[0]["id"]
            status = "SUCCESS" if success else "FAILED"
            logger.info(f"Recorded login attempt: {login_type} ({status}) -> {login_id}")
            return login_id

        except Exception as e:
            logger.error(f"Failed to record login attempt: {e}")
            raise

    async def get_last_successful_fresh_login(
        self,
        account_type: str = "dummy",
    ) -> Optional[datetime]:
        """
        Get timestamp of last successful fresh login.

        Args:
            account_type: Account type to query (default: 'dummy')

        Returns:
            Timestamp of last successful fresh login, or None if never logged in.
        """
        try:
            await self._ensure_connection()

            result = self.client.table("login_history").select(
                "attempted_at"
            ).eq(
                "account_type", account_type
            ).eq(
                "login_type", "fresh"
            ).eq(
                "success", True
            ).order(
                "attempted_at", desc=True
            ).limit(1).execute()

            if result.data:
                timestamp_str = result.data[0]["attempted_at"]
                # Parse ISO format timestamp
                if timestamp_str:
                    # Handle timezone-aware timestamp from Supabase
                    return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))

            return None

        except Exception as e:
            logger.error(f"Failed to get last successful fresh login: {e}")
            return None

    async def get_login_cooldown_remaining(
        self,
        account_type: str = "dummy",
        cooldown_hours: int = 3,
    ) -> int:
        """
        Calculate seconds remaining in login cooldown.

        Args:
            account_type: Account type to query (default: 'dummy')
            cooldown_hours: Cooldown period in hours (default: 3)

        Returns:
            Seconds remaining in cooldown, or 0 if cooldown expired/no history.
        """
        try:
            last_login = await self.get_last_successful_fresh_login(account_type)

            if last_login is None:
                # First-ever login, no cooldown
                return 0

            # Ensure last_login is timezone-aware
            if last_login.tzinfo is None:
                last_login = last_login.replace(tzinfo=timezone.utc)

            cooldown_expires = last_login + timedelta(hours=cooldown_hours)
            now = datetime.now(timezone.utc)

            if now >= cooldown_expires:
                # Cooldown expired
                return 0

            remaining = (cooldown_expires - now).total_seconds()
            return int(remaining)

        except Exception as e:
            logger.error(f"Failed to calculate login cooldown: {e}")
            return 0  # On error, allow login (graceful degradation)

    async def get_login_stats(
        self,
        account_type: str = "dummy",
        days: int = 7,
    ) -> dict:
        """
        Get login statistics for monitoring.

        Args:
            account_type: Account type to query (default: 'dummy')
            days: Number of days to look back (default: 7)

        Returns:
            Dictionary with login statistics.
        """
        try:
            await self._ensure_connection()

            cutoff = datetime.now(timezone.utc) - timedelta(days=days)

            # Get all logins in period
            all_logins = self.client.table("login_history").select(
                "id", count="exact"
            ).eq(
                "account_type", account_type
            ).gte(
                "attempted_at", cutoff.isoformat()
            ).execute()

            # Fresh logins
            fresh_logins = self.client.table("login_history").select(
                "id", count="exact"
            ).eq(
                "account_type", account_type
            ).eq(
                "login_type", "fresh"
            ).gte(
                "attempted_at", cutoff.isoformat()
            ).execute()

            # Successful fresh logins
            successful_fresh = self.client.table("login_history").select(
                "id", count="exact"
            ).eq(
                "account_type", account_type
            ).eq(
                "login_type", "fresh"
            ).eq(
                "success", True
            ).gte(
                "attempted_at", cutoff.isoformat()
            ).execute()

            # Failed logins
            failed_logins = self.client.table("login_history").select(
                "id", count="exact"
            ).eq(
                "account_type", account_type
            ).eq(
                "success", False
            ).gte(
                "attempted_at", cutoff.isoformat()
            ).execute()

            # Current cooldown status
            cooldown_remaining = await self.get_login_cooldown_remaining(account_type)
            last_fresh_login = await self.get_last_successful_fresh_login(account_type)

            return {
                "period_days": days,
                "total_attempts": all_logins.count or 0,
                "fresh_logins": fresh_logins.count or 0,
                "cookie_restores": (all_logins.count or 0) - (fresh_logins.count or 0),
                "successful_fresh": successful_fresh.count or 0,
                "failed_attempts": failed_logins.count or 0,
                "cooldown_active": cooldown_remaining > 0,
                "cooldown_remaining_seconds": cooldown_remaining,
                "last_fresh_login": last_fresh_login.isoformat() if last_fresh_login else None,
            }

        except Exception as e:
            logger.error(f"Failed to get login stats: {e}")
            return {
                "period_days": days,
                "total_attempts": 0,
                "fresh_logins": 0,
                "cookie_restores": 0,
                "successful_fresh": 0,
                "failed_attempts": 0,
                "cooldown_active": False,
                "cooldown_remaining_seconds": 0,
                "last_fresh_login": None,
                "error": str(e),
            }
