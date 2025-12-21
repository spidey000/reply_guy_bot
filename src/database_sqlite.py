"""
SQLite Database - Local fallback for Supabase.

This module provides a SQLite-based database for local development and testing,
or as a fallback when Supabase is unavailable.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default SQLite database path
DEFAULT_DB_PATH = Path("reply_bot.db")


class SQLiteDatabase:
    """
    SQLite implementation of the database interface.
    
    Provides the same API as the Supabase Database class for seamless fallback.
    """
    
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        """Initialize SQLite database."""
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
        self._is_connected = False
        
        self._connect()
        self._create_tables()
        logger.info(f"SQLite database initialized: {db_path}")
    
    def _connect(self) -> None:
        """Establish database connection."""
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self._is_connected = True
            logger.info("SQLite connection established")
        except Exception as e:
            logger.error(f"Failed to connect to SQLite: {e}")
            self._is_connected = False
            raise
    
    def _create_tables(self) -> None:
        """Create database tables if they don't exist."""
        cursor = self.conn.cursor()
        
        # Tweet queue table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tweet_queue (
                id TEXT PRIMARY KEY,
                target_tweet_id TEXT NOT NULL UNIQUE,
                target_author TEXT NOT NULL,
                target_content TEXT,
                reply_text TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                scheduled_at TEXT,
                posted_at TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                error TEXT
            )
        """)
        
        # Target accounts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS target_accounts (
                id TEXT PRIMARY KEY,
                handle TEXT NOT NULL UNIQUE,
                enabled INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Failed tweets (Dead Letter Queue)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS failed_tweets (
                id TEXT PRIMARY KEY,
                tweet_queue_id TEXT,
                target_tweet_id TEXT NOT NULL,
                error TEXT,
                retry_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                last_retry_at TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)
        
        # Login history
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS login_history (
                id TEXT PRIMARY KEY,
                account_type TEXT NOT NULL,
                login_type TEXT NOT NULL,
                success INTEGER NOT NULL,
                error_message TEXT,
                error_type TEXT,
                attempted_at TEXT DEFAULT CURRENT_TIMESTAMP,
                cookies_existed INTEGER DEFAULT 0,
                cookies_valid INTEGER
            )
        """)
        
        self.conn.commit()
        logger.info("SQLite tables created/verified")
    
    def _generate_uuid(self) -> str:
        """Generate a UUID for primary keys."""
        import uuid
        return str(uuid.uuid4())
    
    async def _ensure_connection(self) -> None:
        """Ensure database connection is active."""
        if not self._is_connected or self.conn is None:
            self._connect()
    
    async def health_check(self) -> bool:
        """Check database connection health."""
        try:
            await self._ensure_connection()
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"SQLite health check failed: {e}")
            return False
    
    # =========================================================================
    # Tweet Queue Operations
    # =========================================================================

    async def check_target_tweet_exists(self, target_tweet_id: str) -> bool:
        """Check if a tweet ID already exists in the queue."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tweet_queue WHERE target_tweet_id = ?", (target_tweet_id,))
        return cursor.fetchone()[0] > 0
    
    async def add_to_queue(
        self,
        target_tweet_id: str,
        target_author: str,
        target_content: str,
        reply_text: str,
    ) -> str:
        """Add a new tweet to the queue."""
        await self._ensure_connection()
        
        # Check existence first
        cursor = self.conn.cursor()
        cursor.execute("SELECT id FROM tweet_queue WHERE target_tweet_id = ?", (target_tweet_id,))
        existing = cursor.fetchone()
        
        if existing:
            logger.warning(f"Tweet {target_tweet_id} already in queue ({existing[0]}), skipping add")
            return existing[0]
        
        try:
            tweet_id = self._generate_uuid()
            cursor.execute("""
                INSERT INTO tweet_queue (id, target_tweet_id, target_author, target_content, reply_text, status)
                VALUES (?, ?, ?, ?, ?, 'pending')
            """, (tweet_id, target_tweet_id, target_author, target_content, reply_text))
            self.conn.commit()
            
            logger.info(f"Added tweet to queue: {tweet_id}")
            return tweet_id
        except sqlite3.IntegrityError:
             # Handle race condition
            cursor.execute("SELECT id FROM tweet_queue WHERE target_tweet_id = ?", (target_tweet_id,))
            existing = cursor.fetchone()
            if existing:
                return existing[0]
            raise
    
    async def approve_tweet(self, tweet_id: str, scheduled_at: datetime) -> None:
        """Approve a tweet and schedule it for posting."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tweet_queue SET status = 'approved', scheduled_at = ?
            WHERE id = ?
        """, (scheduled_at.isoformat(), tweet_id))
        self.conn.commit()
        
        logger.info(f"Approved tweet {tweet_id} for {scheduled_at}")
    
    async def reject_tweet(self, tweet_id: str) -> None:
        """Reject and remove a tweet from queue."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("UPDATE tweet_queue SET status = 'rejected' WHERE id = ?", (tweet_id,))
        self.conn.commit()
        
        logger.info(f"Rejected tweet {tweet_id}")
    
    async def get_pending_tweets(self, before: datetime | None = None) -> list[dict]:
        """Get tweets ready for posting."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        if before:
            cursor.execute("""
                SELECT * FROM tweet_queue 
                WHERE status = 'approved' AND posted_at IS NULL AND scheduled_at <= ?
                ORDER BY scheduled_at
            """, (before.isoformat(),))
        else:
            cursor.execute("""
                SELECT * FROM tweet_queue 
                WHERE status = 'approved' AND posted_at IS NULL
                ORDER BY scheduled_at
            """)
        
        return [dict(row) for row in cursor.fetchall()]
    
    async def mark_as_posted(self, tweet_id: str) -> None:
        """Mark a tweet as successfully posted."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tweet_queue SET status = 'posted', posted_at = ?
            WHERE id = ?
        """, (datetime.now().isoformat(), tweet_id))
        self.conn.commit()
        
        logger.info(f"Marked tweet {tweet_id} as posted")
    
    async def mark_as_failed(self, tweet_id: str, error: str) -> None:
        """Mark a tweet as failed."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tweet_queue SET status = 'failed', error = ?
            WHERE id = ?
        """, (error, tweet_id))
        self.conn.commit()
        
        logger.error(f"Marked tweet {tweet_id} as failed: {error}")
    
    async def get_pending_count(self) -> int:
        """Get count of pending tweets in queue."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM tweet_queue 
            WHERE status = 'approved' AND posted_at IS NULL
        """)
        return cursor.fetchone()[0]
    
    async def get_posted_today_count(self) -> int:
        """Get count of tweets posted today."""
        await self._ensure_connection()
        
        today = datetime.now().replace(hour=0, minute=0, second=0).isoformat()
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM tweet_queue 
            WHERE status = 'posted' AND posted_at >= ?
        """, (today,))
        return cursor.fetchone()[0]
    
    # =========================================================================
    # Target Accounts Operations
    # =========================================================================
    
    async def get_target_accounts(self) -> list[str]:
        """Get list of accounts to monitor."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT handle FROM target_accounts WHERE enabled = 1")
        return [row[0] for row in cursor.fetchall()]
    
    async def add_target_account(self, handle: str) -> str:
        """Add a new account to monitor."""
        await self._ensure_connection()
        handle = handle.lower().replace("@", "")
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT enabled FROM target_accounts WHERE handle = ?", (handle,))
        existing = cursor.fetchone()
        
        if existing:
            if existing[0]:
                return "already_active"
            else:
                cursor.execute("UPDATE target_accounts SET enabled = 1 WHERE handle = ?", (handle,))
                self.conn.commit()
                return "re-enabled"
        else:
            cursor.execute("""
                INSERT INTO target_accounts (id, handle, enabled)
                VALUES (?, ?, 1)
            """, (self._generate_uuid(), handle))
            self.conn.commit()
            logger.info(f"Added target account: @{handle}")
            return "added"
    
    async def remove_target_account(self, handle: str) -> None:
        """Remove an account from monitoring."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE target_accounts SET enabled = 0
            WHERE handle = ?
        """, (handle.lower().replace("@", ""),))
        self.conn.commit()
        
        logger.info(f"Removed target account: @{handle}")
    
    # =========================================================================
    # Dead Letter Queue Operations
    # =========================================================================
    
    async def add_to_dead_letter_queue(
        self,
        tweet_queue_id: str,
        target_tweet_id: str,
        error: str,
        retry_count: int = 0,
    ) -> str:
        """Add failed tweet to dead letter queue."""
        await self._ensure_connection()
        
        dlq_id = self._generate_uuid()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO failed_tweets (id, tweet_queue_id, target_tweet_id, error, retry_count, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
        """, (dlq_id, tweet_queue_id, target_tweet_id, error, retry_count))
        self.conn.commit()
        
        logger.info(f"Added to dead letter queue: {dlq_id}")
        return dlq_id
    
    async def get_dead_letter_items(self, max_items: int = 10, max_retry_count: int = 5) -> list[dict]:
        """Get items from dead letter queue ready for retry."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM failed_tweets 
            WHERE status = 'pending' AND retry_count < ?
            ORDER BY created_at
            LIMIT ?
        """, (max_retry_count, max_items))
        
        return [dict(row) for row in cursor.fetchall()]
    
    async def retry_dead_letter_item(self, item_id: str, success: bool, error: Optional[str] = None) -> None:
        """Update dead letter queue item after retry attempt."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        if success:
            cursor.execute("""
                UPDATE failed_tweets SET status = 'retried_successfully', last_retry_at = ?
                WHERE id = ?
            """, (datetime.now().isoformat(), item_id))
        else:
            cursor.execute("SELECT retry_count FROM failed_tweets WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row:
                new_count = row[0] + 1
                status = "exhausted" if new_count >= 5 else "pending"
                cursor.execute("""
                    UPDATE failed_tweets SET retry_count = ?, last_retry_at = ?, error = ?, status = ?
                    WHERE id = ?
                """, (new_count, datetime.now().isoformat(), error or "Retry failed", status, item_id))
        
        self.conn.commit()
    
    async def get_dead_letter_stats(self) -> dict:
        """Get statistics about dead letter queue."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM failed_tweets WHERE status = 'pending'")
        pending = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM failed_tweets WHERE status = 'exhausted'")
        exhausted = cursor.fetchone()[0]
        
        return {"pending": pending, "exhausted": exhausted, "total": pending + exhausted}
    
    # =========================================================================
    # Crash Recovery
    # =========================================================================
    
    async def recover_stale_tweets(self, timeout_minutes: int = 30) -> int:
        """Recover tweets that were being processed but crashed/stalled."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tweet_queue SET status = 'approved'
            WHERE status = 'failed' AND posted_at IS NULL
        """)
        self.conn.commit()
        
        recovered = cursor.rowcount
        if recovered > 0:
            logger.info(f"Recovered {recovered} stale/failed tweets")
        return recovered
    
    # =========================================================================
    # Login Tracking
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
        """Record a login attempt for tracking."""
        await self._ensure_connection()
        
        login_id = self._generate_uuid()
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO login_history 
            (id, account_type, login_type, success, error_message, error_type, cookies_existed, cookies_valid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (login_id, account_type, login_type, int(success), error_message, error_type, 
              int(cookies_existed), int(cookies_valid) if cookies_valid is not None else None))
        self.conn.commit()
        
        status = "SUCCESS" if success else "FAILED"
        logger.info(f"Recorded login attempt: {login_type} ({status}) -> {login_id}")
        return login_id
    
    async def get_last_successful_fresh_login(self, account_type: str = "dummy") -> Optional[datetime]:
        """Get timestamp of last successful fresh login."""
        await self._ensure_connection()
        
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT attempted_at FROM login_history
            WHERE account_type = ? AND login_type = 'fresh' AND success = 1
            ORDER BY attempted_at DESC LIMIT 1
        """, (account_type,))
        
        row = cursor.fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None
    
    async def get_login_cooldown_remaining(self, account_type: str = "dummy", cooldown_hours: int = 3) -> int:
        """Calculate seconds remaining in login cooldown."""
        last_login = await self.get_last_successful_fresh_login(account_type)
        if last_login is None:
            return 0
        
        if last_login.tzinfo is None:
            last_login = last_login.replace(tzinfo=timezone.utc)
        
        cooldown_expires = last_login + timedelta(hours=cooldown_hours)
        now = datetime.now(timezone.utc)
        
        if now >= cooldown_expires:
            return 0
        
        return int((cooldown_expires - now).total_seconds())
    
    async def get_login_stats(self, account_type: str = "dummy", days: int = 7) -> dict:
        """Get login statistics for monitoring."""
        await self._ensure_connection()
        
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cursor = self.conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM login_history
            WHERE account_type = ? AND attempted_at >= ?
        """, (account_type, cutoff))
        total = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM login_history
            WHERE account_type = ? AND login_type = 'fresh' AND attempted_at >= ?
        """, (account_type, cutoff))
        fresh = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT COUNT(*) FROM login_history
            WHERE account_type = ? AND success = 1 AND attempted_at >= ?
        """, (account_type, cutoff))
        successful = cursor.fetchone()[0]
        
        return {
            "total_attempts": total,
            "fresh_logins": fresh,
            "successful": successful,
            "success_rate": successful / total if total > 0 else 0,
        }
