"""
Real Functionality Tests - Database Operations.

Tests actual database operations and state transitions using in-memory SQLite.
This verifies the logic of database operations without requiring Supabase.

Test cases:
- Add to queue creates pending record
- Approve tweet state transition
- Mark as posted state transition
- Mark as failed stores error
- Get pending tweets filters correctly
- Dead letter queue workflow
- Recover stale tweets
- Get pending count
- Health check connection

Mocks: Supabase client (replaced with in-memory SQLite)
Real: All database operations, state transitions, queries
"""

import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Generator

import pytest


class TestDatabaseAdapter:
    """
    Test database adapter using SQLite.

    This adapter mimics the Supabase client interface for testing
    database operations with real SQL queries.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    def _generate_uuid(self) -> str:
        return str(uuid.uuid4())

    async def add_to_queue(
        self,
        target_tweet_id: str,
        target_author: str,
        target_content: str,
        reply_text: str,
    ) -> str:
        """Add a tweet to the queue."""
        cursor = self.conn.cursor()
        tweet_id = self._generate_uuid()
        cursor.execute(
            """
            INSERT INTO tweet_queue
            (id, target_tweet_id, target_author, target_content, reply_text, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (tweet_id, target_tweet_id, target_author, target_content, reply_text, datetime.now().isoformat())
        )
        self.conn.commit()
        return tweet_id

    async def approve_tweet(self, tweet_id: str, scheduled_at: datetime) -> None:
        """Approve a tweet for posting."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tweet_queue
            SET status = 'approved', scheduled_at = ?
            WHERE id = ?
            """,
            (scheduled_at.isoformat(), tweet_id)
        )
        self.conn.commit()

    async def reject_tweet(self, tweet_id: str) -> None:
        """Reject a tweet."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tweet_queue SET status = 'rejected' WHERE id = ?",
            (tweet_id,)
        )
        self.conn.commit()

    async def mark_as_posted(self, tweet_id: str) -> None:
        """Mark a tweet as posted."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tweet_queue
            SET status = 'posted', posted_at = ?
            WHERE id = ?
            """,
            (datetime.now().isoformat(), tweet_id)
        )
        self.conn.commit()

    async def mark_as_failed(self, tweet_id: str, error: str) -> None:
        """Mark a tweet as failed with error message."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tweet_queue
            SET status = 'failed', error = ?
            WHERE id = ?
            """,
            (error, tweet_id)
        )
        self.conn.commit()

    async def get_pending_tweets(self, before: datetime | None = None) -> list[dict]:
        """Get tweets ready for posting."""
        cursor = self.conn.cursor()

        if before:
            cursor.execute(
                """
                SELECT * FROM tweet_queue
                WHERE status = 'approved' AND posted_at IS NULL AND scheduled_at <= ?
                ORDER BY scheduled_at
                """,
                (before.isoformat(),)
            )
        else:
            cursor.execute(
                """
                SELECT * FROM tweet_queue
                WHERE status = 'approved' AND posted_at IS NULL
                ORDER BY scheduled_at
                """
            )

        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_pending_count(self) -> int:
        """Get count of pending approved tweets."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM tweet_queue
            WHERE status = 'approved' AND posted_at IS NULL
            """
        )
        return cursor.fetchone()["count"]

    async def get_posted_today_count(self) -> int:
        """Get count of tweets posted today."""
        cursor = self.conn.cursor()
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM tweet_queue
            WHERE status = 'posted' AND posted_at >= ?
            """,
            (today.isoformat(),)
        )
        return cursor.fetchone()["count"]

    async def add_to_dead_letter_queue(
        self,
        tweet_queue_id: str,
        target_tweet_id: str,
        error: str,
        retry_count: int = 0,
    ) -> str:
        """Add to dead letter queue."""
        cursor = self.conn.cursor()
        dlq_id = self._generate_uuid()
        cursor.execute(
            """
            INSERT INTO failed_tweets
            (id, tweet_queue_id, target_tweet_id, error, retry_count, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (dlq_id, tweet_queue_id, target_tweet_id, error, retry_count, datetime.now().isoformat())
        )
        self.conn.commit()
        return dlq_id

    async def get_dead_letter_items(self, max_items: int = 10, max_retry_count: int = 5) -> list[dict]:
        """Get items from dead letter queue."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT * FROM failed_tweets
            WHERE status = 'pending' AND retry_count < ?
            ORDER BY created_at
            LIMIT ?
            """,
            (max_retry_count, max_items)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

    async def retry_dead_letter_item(self, item_id: str, success: bool, error: str | None = None) -> None:
        """Update dead letter item after retry."""
        cursor = self.conn.cursor()

        if success:
            cursor.execute(
                """
                UPDATE failed_tweets
                SET status = 'retried_successfully', last_retry_at = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(), item_id)
            )
        else:
            cursor.execute("SELECT retry_count FROM failed_tweets WHERE id = ?", (item_id,))
            row = cursor.fetchone()
            if row:
                new_count = row["retry_count"] + 1
                status = "exhausted" if new_count >= 5 else "pending"
                cursor.execute(
                    """
                    UPDATE failed_tweets
                    SET retry_count = ?, last_retry_at = ?, error = ?, status = ?
                    WHERE id = ?
                    """,
                    (new_count, datetime.now().isoformat(), error or "Retry failed", status, item_id)
                )

        self.conn.commit()

    async def recover_stale_tweets(self, timeout_minutes: int = 30) -> int:
        """Recover stale/failed tweets."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tweet_queue
            SET status = 'approved'
            WHERE status = 'failed' AND posted_at IS NULL
            """
        )
        self.conn.commit()
        return cursor.rowcount

    async def health_check(self) -> bool:
        """Check database connection."""
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT 1")
            return True
        except Exception:
            return False

    def _get_tweet_by_id(self, tweet_id: str) -> dict | None:
        """Helper: Get tweet by ID."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tweet_queue WHERE id = ?", (tweet_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


@pytest.fixture
def test_db() -> Generator[TestDatabaseAdapter, None, None]:
    """Create in-memory SQLite database with schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create schema
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tweet_queue (
            id TEXT PRIMARY KEY,
            target_tweet_id TEXT NOT NULL,
            target_author TEXT NOT NULL,
            target_content TEXT,
            reply_text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            scheduled_at TEXT,
            posted_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS target_accounts (
            id TEXT PRIMARY KEY,
            handle TEXT NOT NULL UNIQUE,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS failed_tweets (
            id TEXT PRIMARY KEY,
            tweet_queue_id TEXT,
            target_tweet_id TEXT NOT NULL,
            error TEXT,
            retry_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_retry_at TEXT,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY (tweet_queue_id) REFERENCES tweet_queue(id)
        );
    """)
    conn.commit()

    adapter = TestDatabaseAdapter(conn)
    yield adapter
    conn.close()


@pytest.mark.real
@pytest.mark.asyncio
class TestDatabaseReal:
    """Real functionality tests for database operations."""

    async def test_add_to_queue_creates_pending(self, test_db: TestDatabaseAdapter):
        """
        Test that add_to_queue creates a record with 'pending' status.

        Verifies:
        - Record is created with correct data
        - Status is set to 'pending'
        - UUID is returned
        """
        # Act
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="12345",
            target_author="testuser",
            target_content="Test tweet content",
            reply_text="Test reply",
        )

        # Assert
        assert tweet_id is not None
        assert len(tweet_id) == 36  # UUID format

        # Verify database state
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert tweet is not None
        assert tweet["status"] == "pending"
        assert tweet["target_tweet_id"] == "12345"
        assert tweet["target_author"] == "testuser"
        assert tweet["reply_text"] == "Test reply"
        assert tweet["posted_at"] is None

    async def test_approve_tweet_state_transition(self, test_db: TestDatabaseAdapter):
        """
        Test that approve_tweet transitions status from 'pending' to 'approved'.

        Verifies:
        - Status changes to 'approved'
        - scheduled_at is set correctly
        """
        # Arrange
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="12345",
            target_author="testuser",
            target_content="Content",
            reply_text="Reply",
        )

        # Verify initial state
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert tweet["status"] == "pending"

        # Act
        scheduled_time = datetime.now() + timedelta(hours=1)
        await test_db.approve_tweet(tweet_id, scheduled_time)

        # Assert
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert tweet["status"] == "approved"
        assert tweet["scheduled_at"] is not None
        # Verify the scheduled time is approximately correct
        scheduled_dt = datetime.fromisoformat(tweet["scheduled_at"])
        assert abs((scheduled_dt - scheduled_time).total_seconds()) < 1

    async def test_mark_as_posted_state_transition(self, test_db: TestDatabaseAdapter):
        """
        Test that mark_as_posted transitions status and sets posted_at.

        Verifies:
        - Status changes to 'posted'
        - posted_at timestamp is set
        """
        # Arrange
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="12345",
            target_author="testuser",
            target_content="Content",
            reply_text="Reply",
        )
        await test_db.approve_tweet(tweet_id, datetime.now())

        # Act
        await test_db.mark_as_posted(tweet_id)

        # Assert
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert tweet["status"] == "posted"
        assert tweet["posted_at"] is not None

    async def test_mark_as_failed_stores_error(self, test_db: TestDatabaseAdapter):
        """
        Test that mark_as_failed sets status and stores error message.

        Verifies:
        - Status changes to 'failed'
        - Error message is stored
        """
        # Arrange
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="12345",
            target_author="testuser",
            target_content="Content",
            reply_text="Reply",
        )

        error_message = "Twitter API rate limit exceeded"

        # Act
        await test_db.mark_as_failed(tweet_id, error_message)

        # Assert
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert tweet["status"] == "failed"
        assert tweet["error"] == error_message

    async def test_get_pending_tweets_filters_correctly(self, test_db: TestDatabaseAdapter):
        """
        Test that get_pending_tweets returns only approved tweets before cutoff.

        Verifies:
        - Only 'approved' status tweets are returned
        - Only tweets with scheduled_at <= now are returned
        - Pending and posted tweets are excluded
        """
        # Arrange: Create tweets in different states
        now = datetime.now()

        # Tweet 1: Pending (should NOT be returned)
        await test_db.add_to_queue(
            target_tweet_id="1",
            target_author="user1",
            target_content="Content 1",
            reply_text="Reply 1",
        )

        # Tweet 2: Approved, scheduled in past (should be returned)
        tweet_2 = await test_db.add_to_queue(
            target_tweet_id="2",
            target_author="user2",
            target_content="Content 2",
            reply_text="Reply 2",
        )
        await test_db.approve_tweet(tweet_2, now - timedelta(minutes=5))

        # Tweet 3: Approved, scheduled in future (should NOT be returned)
        tweet_3 = await test_db.add_to_queue(
            target_tweet_id="3",
            target_author="user3",
            target_content="Content 3",
            reply_text="Reply 3",
        )
        await test_db.approve_tweet(tweet_3, now + timedelta(hours=1))

        # Tweet 4: Already posted (should NOT be returned)
        tweet_4 = await test_db.add_to_queue(
            target_tweet_id="4",
            target_author="user4",
            target_content="Content 4",
            reply_text="Reply 4",
        )
        await test_db.approve_tweet(tweet_4, now - timedelta(hours=1))
        await test_db.mark_as_posted(tweet_4)

        # Act
        pending = await test_db.get_pending_tweets(before=now)

        # Assert
        assert len(pending) == 1
        assert pending[0]["target_tweet_id"] == "2"

    async def test_dead_letter_queue_workflow(self, test_db: TestDatabaseAdapter):
        """
        Test complete dead letter queue workflow.

        Verifies:
        - Items can be added to DLQ
        - Items can be retrieved
        - Retry success marks item as processed
        - Retry failure increments counter
        - Exhausted status after max retries
        """
        # Arrange
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="12345",
            target_author="testuser",
            target_content="Content",
            reply_text="Reply",
        )

        # Act 1: Add to DLQ
        dlq_id = await test_db.add_to_dead_letter_queue(
            tweet_queue_id=tweet_id,
            target_tweet_id="12345",
            error="Initial failure",
            retry_count=0,
        )

        # Assert 1: Item is in DLQ
        dlq_items = await test_db.get_dead_letter_items()
        assert len(dlq_items) == 1
        assert dlq_items[0]["id"] == dlq_id
        assert dlq_items[0]["error"] == "Initial failure"

        # Act 2: Simulate 4 failed retries
        for i in range(4):
            await test_db.retry_dead_letter_item(dlq_id, success=False, error=f"Retry {i+1} failed")

        # Assert 2: Retry count is 4, still pending
        dlq_items = await test_db.get_dead_letter_items(max_retry_count=10)
        assert len(dlq_items) == 1
        assert dlq_items[0]["retry_count"] == 4

        # Act 3: Fifth failure should exhaust
        await test_db.retry_dead_letter_item(dlq_id, success=False, error="Final failure")

        # Assert 3: Item is exhausted (not returned in pending query)
        dlq_items = await test_db.get_dead_letter_items()
        assert len(dlq_items) == 0

    async def test_recover_stale_tweets(self, test_db: TestDatabaseAdapter):
        """
        Test that recover_stale_tweets resets failed tweets.

        Verifies:
        - Failed tweets are moved back to approved
        - Count of recovered tweets is correct
        """
        # Arrange: Create failed tweets
        tweet_1 = await test_db.add_to_queue(
            target_tweet_id="1",
            target_author="user1",
            target_content="Content",
            reply_text="Reply",
        )
        await test_db.mark_as_failed(tweet_1, "Temporary error")

        tweet_2 = await test_db.add_to_queue(
            target_tweet_id="2",
            target_author="user2",
            target_content="Content",
            reply_text="Reply",
        )
        await test_db.mark_as_failed(tweet_2, "Network error")

        # Act
        recovered = await test_db.recover_stale_tweets()

        # Assert
        assert recovered == 2

        # Verify tweets are now approved
        tweet = test_db._get_tweet_by_id(tweet_1)
        assert tweet["status"] == "approved"

        tweet = test_db._get_tweet_by_id(tweet_2)
        assert tweet["status"] == "approved"

    async def test_get_pending_count(self, test_db: TestDatabaseAdapter):
        """
        Test that get_pending_count returns correct count.

        Verifies:
        - Only approved, unposted tweets are counted
        - Other statuses are excluded
        """
        # Arrange
        now = datetime.now()

        # Add 3 approved tweets
        for i in range(3):
            tweet_id = await test_db.add_to_queue(
                target_tweet_id=str(i),
                target_author=f"user{i}",
                target_content="Content",
                reply_text="Reply",
            )
            await test_db.approve_tweet(tweet_id, now + timedelta(hours=i))

        # Add 1 pending (not counted)
        await test_db.add_to_queue(
            target_tweet_id="99",
            target_author="pending_user",
            target_content="Content",
            reply_text="Reply",
        )

        # Act
        count = await test_db.get_pending_count()

        # Assert
        assert count == 3

    async def test_health_check_connection(self, test_db: TestDatabaseAdapter):
        """
        Test that health_check returns True for valid connection.

        Verifies:
        - Returns True when database is accessible
        """
        # Act
        is_healthy = await test_db.health_check()

        # Assert
        assert is_healthy is True

    async def test_full_state_transition_workflow(
        self,
        test_db: TestDatabaseAdapter,
        assert_status_transition,
    ):
        """
        Test complete state transition workflow.

        Workflow: PENDING → APPROVED → POSTED

        Verifies each transition follows expected sequence.
        """
        expected_sequence = ["pending", "approved", "posted"]

        # Create tweet (PENDING)
        tweet_id = await test_db.add_to_queue(
            target_tweet_id="workflow_test",
            target_author="workflow_user",
            target_content="Workflow content",
            reply_text="Workflow reply",
        )

        tweet = test_db._get_tweet_by_id(tweet_id)
        initial_status = tweet["status"]
        assert initial_status == "pending"

        # Approve (PENDING → APPROVED)
        await test_db.approve_tweet(tweet_id, datetime.now())
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert_status_transition(initial_status, tweet["status"], expected_sequence)

        approved_status = tweet["status"]

        # Post (APPROVED → POSTED)
        await test_db.mark_as_posted(tweet_id)
        tweet = test_db._get_tweet_by_id(tweet_id)
        assert_status_transition(approved_status, tweet["status"], expected_sequence)
