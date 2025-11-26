"""
Real Functionality Tests - Background Worker.

Tests actual background worker behavior with mocked dependencies:
- Finding ready tweets for processing
- Successful publication flow
- Failed publication adds to DLQ
- Exception handling in worker
- Queue status reporting

Mocks: Database, Ghost Delegate, Telegram client
Real: Worker loop logic, error handling, state flow coordination
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.background_worker import (
    run_worker,
    process_pending_tweets,
    _publish_tweet,
    _notify_published,
    get_queue_status,
)


@pytest.fixture
def mock_db():
    """Create mock database client."""
    db = AsyncMock()
    db.get_pending_tweets = AsyncMock(return_value=[])
    db.get_pending_count = AsyncMock(return_value=0)
    db.get_posted_today_count = AsyncMock(return_value=0)
    db.mark_as_posted = AsyncMock()
    db.mark_as_failed = AsyncMock()
    db.add_to_dead_letter_queue = AsyncMock(return_value="dlq-id")
    return db


@pytest.fixture
def mock_ghost():
    """Create mock Ghost Delegate."""
    ghost = AsyncMock()
    ghost.post_as_main = AsyncMock(return_value=True)
    return ghost


@pytest.fixture
def mock_telegram():
    """Create mock Telegram client."""
    telegram = AsyncMock()
    telegram.send_published_notification = AsyncMock()
    return telegram


@pytest.fixture
def sample_pending_tweets():
    """Create sample pending tweets."""
    now = datetime.now()
    return [
        {
            "id": "tweet-1",
            "target_tweet_id": "target-1",
            "reply_text": "Reply 1",
            "status": "approved",
            "scheduled_at": (now - timedelta(minutes=5)).isoformat(),
        },
        {
            "id": "tweet-2",
            "target_tweet_id": "target-2",
            "reply_text": "Reply 2",
            "status": "approved",
            "scheduled_at": (now - timedelta(minutes=10)).isoformat(),
        },
    ]


@pytest.mark.real
@pytest.mark.asyncio
class TestBackgroundWorkerReal:
    """Real functionality tests for the background worker module."""

    async def test_process_pending_tweets_finds_ready_tweets(
        self,
        mock_db,
        mock_ghost,
        mock_telegram,
        sample_pending_tweets,
    ):
        """
        Test that process_pending_tweets finds and processes ready tweets.

        Should query database for tweets where scheduled_at <= now
        and process each one.
        """
        # Arrange
        mock_db.get_pending_tweets.return_value = sample_pending_tweets

        # Act
        processed = await process_pending_tweets(mock_db, mock_ghost, mock_telegram)

        # Assert
        assert processed == 2
        assert mock_db.get_pending_tweets.called
        assert mock_ghost.post_as_main.call_count == 2
        assert mock_db.mark_as_posted.call_count == 2

    async def test_successful_publication_flow(
        self,
        mock_db,
        mock_ghost,
        mock_telegram,
    ):
        """
        Test successful tweet publication updates database correctly.

        Flow: post_as_main succeeds → mark_as_posted called
        """
        # Arrange
        tweet = {
            "id": "tweet-123",
            "target_tweet_id": "target-456",
            "reply_text": "Test reply",
        }
        mock_ghost.post_as_main.return_value = True

        # Act
        success = await _publish_tweet(tweet, mock_ghost, mock_db)

        # Assert
        assert success is True
        mock_ghost.post_as_main.assert_called_once_with("target-456", "Test reply")
        mock_db.mark_as_posted.assert_called_once_with("tweet-123")
        mock_db.mark_as_failed.assert_not_called()
        mock_db.add_to_dead_letter_queue.assert_not_called()

    async def test_failed_publication_adds_to_dlq(
        self,
        mock_db,
        mock_ghost,
    ):
        """
        Test that failed publication adds tweet to dead letter queue.

        Flow: post_as_main fails → mark_as_failed + add_to_dead_letter_queue
        """
        # Arrange
        tweet = {
            "id": "tweet-123",
            "target_tweet_id": "target-456",
            "reply_text": "Test reply",
        }
        mock_ghost.post_as_main.return_value = False

        # Act
        success = await _publish_tweet(tweet, mock_ghost, mock_db)

        # Assert
        assert success is False
        mock_db.mark_as_failed.assert_called_once()
        mock_db.add_to_dead_letter_queue.assert_called_once()

        # Verify DLQ call parameters
        dlq_call = mock_db.add_to_dead_letter_queue.call_args
        assert dlq_call.kwargs["tweet_queue_id"] == "tweet-123"
        assert dlq_call.kwargs["target_tweet_id"] == "target-456"
        assert dlq_call.kwargs["retry_count"] == 0

    async def test_exception_handling_in_worker(
        self,
        mock_db,
        mock_ghost,
    ):
        """
        Test that exceptions during publication are properly handled.

        Exceptions should:
        - Mark tweet as failed
        - Add to dead letter queue
        - Not crash the worker
        """
        # Arrange
        tweet = {
            "id": "tweet-123",
            "target_tweet_id": "target-456",
            "reply_text": "Test reply",
        }
        mock_ghost.post_as_main.side_effect = Exception("Network error")

        # Act
        success = await _publish_tweet(tweet, mock_ghost, mock_db)

        # Assert
        assert success is False
        mock_db.mark_as_failed.assert_called_once()

        # Check error message contains exception info
        fail_call = mock_db.mark_as_failed.call_args
        assert "Network error" in fail_call.kwargs.get("error", fail_call.args[1] if len(fail_call.args) > 1 else "")

        mock_db.add_to_dead_letter_queue.assert_called_once()

    async def test_get_queue_status(self, mock_db):
        """
        Test that get_queue_status returns accurate status.

        Should return pending count and posted today count.
        """
        # Arrange
        mock_db.get_pending_count.return_value = 5
        mock_db.get_posted_today_count.return_value = 12

        # Act
        status = await get_queue_status(mock_db)

        # Assert
        assert status["pending"] == 5
        assert status["posted_today"] == 12
        assert "next_check" in status

    async def test_notification_sent_on_success(
        self,
        mock_db,
        mock_ghost,
        mock_telegram,
        sample_pending_tweets,
    ):
        """
        Test that Telegram notification is sent after successful publication.

        When telegram client is provided, _notify_published should be called.
        """
        # Arrange: Single tweet for simplicity
        mock_db.get_pending_tweets.return_value = [sample_pending_tweets[0]]
        mock_ghost.post_as_main.return_value = True

        # Act
        processed = await process_pending_tweets(mock_db, mock_ghost, mock_telegram)

        # Assert
        assert processed == 1
        mock_telegram.send_published_notification.assert_called_once()

    async def test_no_notification_on_failure(
        self,
        mock_db,
        mock_ghost,
        mock_telegram,
        sample_pending_tweets,
    ):
        """
        Test that notification is NOT sent when publication fails.
        """
        # Arrange
        mock_db.get_pending_tweets.return_value = [sample_pending_tweets[0]]
        mock_ghost.post_as_main.return_value = False

        # Act
        processed = await process_pending_tweets(mock_db, mock_ghost, mock_telegram)

        # Assert
        assert processed == 0
        mock_telegram.send_published_notification.assert_not_called()

    async def test_notify_published_handles_exception(
        self,
        mock_telegram,
    ):
        """
        Test that _notify_published handles exceptions gracefully.

        Should not raise exceptions even if Telegram notification fails.
        """
        # Arrange
        tweet = {"id": "test-tweet", "reply_text": "Test"}
        mock_telegram.send_published_notification.side_effect = Exception("Telegram API error")

        # Act & Assert: Should not raise
        await _notify_published(tweet, mock_telegram)

    async def test_empty_queue_processing(
        self,
        mock_db,
        mock_ghost,
        mock_telegram,
    ):
        """
        Test that processing empty queue returns 0.

        When no pending tweets, should return 0 without errors.
        """
        # Arrange
        mock_db.get_pending_tweets.return_value = []

        # Act
        processed = await process_pending_tweets(mock_db, mock_ghost, mock_telegram)

        # Assert
        assert processed == 0
        mock_ghost.post_as_main.assert_not_called()
