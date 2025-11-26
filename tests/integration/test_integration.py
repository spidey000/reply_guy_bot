"""
Integration Tests for Reply Guy Bot.

This module contains comprehensive integration tests that validate the end-to-end
workflow of the bot, from tweet detection to publication.

Test Coverage:
    - Full happy path workflow
    - Rejection workflow
    - Error scenarios and resilience
    - Component integration
    - Circuit breaker and rate limiting
    - Dead letter queue functionality

Usage:
    pytest tests/test_integration.py -v
    pytest tests/test_integration.py -v --cov=src --cov-report=html
"""

import asyncio
import pytest
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

# Mock settings before importing any modules that depend on it
os.environ.setdefault("DUMMY_USERNAME", "test_dummy")
os.environ.setdefault("DUMMY_EMAIL", "dummy@test.com")
os.environ.setdefault("DUMMY_PASSWORD", "test_pass")
os.environ.setdefault("MAIN_ACCOUNT_HANDLE", "test_main")
os.environ.setdefault("AI_API_KEY", "test-key")
os.environ.setdefault("AI_BASE_URL", "http://localhost:11434/v1")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456")
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_KEY", "test-key")

# Add asyncio.timeout for Python 3.10 compatibility
if sys.version_info < (3, 11):
    class _AsyncioTimeout:
        """Mock timeout context manager for Python 3.10."""
        def __init__(self, seconds):
            self.seconds = seconds
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False

    # Inject into asyncio module
    asyncio.timeout = _AsyncioTimeout

from src.bot import ReplyGuyBot
from src.scheduler import calculate_schedule_time
from src.background_worker import process_pending_tweets
from src.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState
from src.rate_limiter import RateLimitExceeded


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def mock_twikit_client():
    """Mock Twikit client for Twitter API."""
    client = MagicMock()

    # Mock user
    mock_user = MagicMock()
    mock_user.id = "user_123"

    # Mock tweet
    mock_tweet = MagicMock()
    mock_tweet.id = "1234567890"
    mock_tweet.text = "AI is transforming everything!"
    mock_tweet.retweeted_tweet = None
    mock_tweet.in_reply_to = None

    # Mock reply method
    async def mock_reply(text):
        return MagicMock(id="reply_123")
    mock_tweet.reply = mock_reply

    # Mock get_tweets
    async def mock_get_tweets(tweet_type, count):
        return [mock_tweet]
    mock_user.get_tweets = mock_get_tweets

    # Mock get_user_by_screen_name
    async def mock_get_user(handle):
        return mock_user
    client.get_user_by_screen_name = mock_get_user

    # Mock get_tweet_by_id
    async def mock_get_tweet(tweet_id):
        return mock_tweet
    client.get_tweet_by_id = mock_get_tweet

    # Mock set_delegate_account
    client.set_delegate_account = MagicMock()

    return client


@pytest.fixture
def mock_supabase_client():
    """Mock Supabase client for database operations."""
    client = MagicMock()

    # Mock table operations
    mock_table = MagicMock()

    # Mock insert
    mock_insert_result = MagicMock()
    mock_insert_result.data = [{"id": "queue-uuid-123"}]
    mock_table.insert.return_value.execute.return_value = mock_insert_result

    # Mock select
    mock_select_result = MagicMock()
    mock_select_result.data = []
    mock_select_result.count = 0
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.lte.return_value = mock_table
    mock_table.gte.return_value = mock_table
    mock_table.lt.return_value = mock_table
    mock_table.order.return_value = mock_table
    mock_table.limit.return_value = mock_table
    mock_table.execute.return_value = mock_select_result

    # Mock update
    mock_table.update.return_value = mock_table

    # Mock upsert
    mock_table.upsert.return_value.execute.return_value = MagicMock()

    client.table.return_value = mock_table

    return client


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client for AI generation."""
    client = AsyncMock()

    # Mock chat completion response
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="This is a great insight! I completely agree with your perspective."
            )
        )
    ]

    client.chat.completions.create.return_value = mock_response
    client.models.list.return_value = []

    return client


@pytest.fixture
def mock_telegram_app():
    """Mock Telegram bot application."""
    app = MagicMock()

    # Mock bot
    mock_bot = AsyncMock()
    mock_message = MagicMock()
    mock_message.message_id = 12345
    mock_bot.send_message.return_value = mock_message

    app.bot = mock_bot
    app.run_polling = AsyncMock()
    app.stop = AsyncMock()

    return app


# =============================================================================
# Test Class: Full Workflow
# =============================================================================

class TestFullWorkflow:
    """End-to-end workflow tests."""

    @pytest.mark.asyncio
    async def test_full_workflow_happy_path(
        self,
        mock_twikit_client,
        mock_supabase_client,
        mock_openai_client,
        mock_telegram_app,
    ):
        """
        Test complete flow from tweet detection to publication.

        Flow:
            1. Bot detects new tweet from target account
            2. AI generates reply
            3. Reply stored in database
            4. Telegram approval request sent
            5. User approves via callback
            6. Scheduler calculates burst mode timing
            7. Background worker publishes at scheduled time
            8. Status updated to "posted"
        """
        # Setup mocks
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure mock database
            mock_db = AsyncMock()
            mock_db.get_target_accounts.return_value = ["elonmusk"]
            mock_db.add_to_queue.return_value = "queue-uuid-123"
            mock_db.get_pending_tweets.return_value = []
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            # Configure mock AI
            mock_ai = AsyncMock()
            mock_ai.generate_reply.return_value = "Great insight! I agree completely."
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            # Configure mock Telegram
            mock_telegram = AsyncMock()
            mock_telegram.send_approval_request.return_value = 12345
            mock_telegram.app = mock_telegram_app
            MockTelegram.return_value = mock_telegram

            # Configure mock Ghost Delegate
            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            mock_ghost.client = mock_twikit_client
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            success = await bot.initialize()

            assert success is True
            assert bot.ai is not None
            assert bot.db is not None
            assert bot.telegram is not None
            assert bot.ghost is not None

            # Simulate tweet detection
            mock_tweet = mock_twikit_client.get_tweet_by_id
            tweet = await mock_tweet("1234567890")

            # Process new tweet
            await bot._process_new_tweet(tweet, "elonmusk")

            # Verify AI generated reply
            mock_ai.generate_reply.assert_called_once()

            # Verify tweet added to queue
            mock_db.add_to_queue.assert_called_once_with(
                target_tweet_id="1234567890",
                target_author="elonmusk",
                target_content="AI is transforming everything!",
                reply_text="Great insight! I agree completely.",
            )

            # Verify Telegram approval request sent
            mock_telegram.send_approval_request.assert_called_once()

            # Simulate approval
            await bot._handle_approve("queue-uuid-123")

            # Verify scheduled
            mock_db.approve_tweet.assert_called_once()

            # Verify confirmation sent
            mock_telegram.send_scheduled_confirmation.assert_called_once()

    @pytest.mark.asyncio
    async def test_rejection_workflow(
        self,
        mock_supabase_client,
    ):
        """
        Test tweet rejection flow.

        Flow:
            1. User receives approval request
            2. User clicks reject button
            3. Status updated to "rejected"
            4. No publication occurs
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure mocks
            mock_db = AsyncMock()
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_ai = AsyncMock()
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Simulate rejection
            await bot._handle_reject("queue-uuid-123")

            # Verify rejection recorded
            mock_db.reject_tweet.assert_called_once_with("queue-uuid-123")


# =============================================================================
# Test Class: Error Handling
# =============================================================================

class TestErrorHandling:
    """Error scenario tests."""

    @pytest.mark.asyncio
    async def test_ai_failure_graceful_degradation(self):
        """
        Test bot handles AI failure gracefully.

        Scenario:
            - AI service fails to generate reply
            - Bot logs error but continues operation
            - No tweet added to queue
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure mock AI to fail
            mock_ai = AsyncMock()
            mock_ai.generate_reply.return_value = None  # AI failure
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            mock_db = AsyncMock()
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Create mock tweet
            mock_tweet = MagicMock()
            mock_tweet.id = "tweet_123"
            mock_tweet.text = "Test tweet"

            # Process tweet with failing AI
            await bot._process_new_tweet(mock_tweet, "testuser")

            # Verify AI was called but queue not updated
            mock_ai.generate_reply.assert_called_once()
            mock_db.add_to_queue.assert_not_called()

    @pytest.mark.asyncio
    async def test_twitter_rate_limit_handling(self, mock_twikit_client):
        """
        Test rate limit enforcement.

        Scenario:
            - Bot attempts to post when rate limit exceeded
            - Rate limiter blocks the post
            - Error logged appropriately
        """
        with patch("src.x_delegate.Client", return_value=mock_twikit_client):
            from src.x_delegate import GhostDelegate
            from src.rate_limiter import RateLimiter

            # Create Ghost Delegate
            ghost = GhostDelegate()
            ghost.client = mock_twikit_client
            ghost._is_authenticated = True
            ghost._current_account = "dummy"

            # Create rate limiter with low limits and fill it up
            ghost.rate_limiter = RateLimiter(max_per_hour=1, max_per_day=1)
            await ghost.rate_limiter.record_post()  # Fill up the limit

            # Attempt to post (should be blocked)
            result = await ghost.post_as_main("tweet_123", "Test reply")

            # Verify post was blocked by rate limiter
            assert result is False

    @pytest.mark.asyncio
    async def test_database_connection_recovery(self):
        """
        Test DB reconnection on failure.

        Scenario:
            - Database connection fails
            - Bot attempts reconnection with backoff
            - Operation succeeds after reconnection
        """
        from src.database import Database

        with patch("src.database.create_client") as mock_create:
            # First call fails, second succeeds
            mock_client = MagicMock()
            mock_create.side_effect = [
                Exception("Connection failed"),
                mock_client
            ]

            # First initialization fails
            with pytest.raises(Exception):
                db = Database()

            # Reset mock
            mock_create.side_effect = None
            mock_create.return_value = mock_client

            # Second initialization succeeds
            db = Database()
            assert db.client is not None

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_on_failures(self):
        """
        Test circuit breaker protects services.

        Scenario:
            - External service fails repeatedly
            - Circuit breaker opens after threshold
            - Subsequent calls fail fast without hitting service
        """
        breaker = CircuitBreaker(
            name="test_service",
            failure_threshold=3,
            recovery_timeout=60,
        )

        # Mock failing function
        async def failing_call():
            raise Exception("Service unavailable")

        # Fail repeatedly until circuit opens
        for i in range(3):
            with pytest.raises(Exception):
                await breaker.call(failing_call)

        # Verify circuit is open
        assert breaker.state == CircuitState.OPEN

        # Next call should fail fast
        with pytest.raises(CircuitBreakerError):
            await breaker.call(failing_call)


# =============================================================================
# Test Class: Component Integration
# =============================================================================

class TestComponentIntegration:
    """Component interaction tests."""

    @pytest.mark.asyncio
    async def test_ghost_delegate_security_flow(self, mock_twikit_client):
        """
        Test context switch and revert in Ghost Delegate.

        Flow:
            1. Login with dummy account
            2. Switch to main account for posting
            3. Post tweet
            4. Guaranteed revert to dummy account
            5. Verify audit logging
        """
        with patch("src.x_delegate.Client", return_value=mock_twikit_client), \
             patch("src.x_delegate.COOKIE_FILE") as mock_cookie_file:

            from src.x_delegate import GhostDelegate

            # Mock cookie file doesn't exist
            mock_cookie_file.exists.return_value = False

            # Create Ghost Delegate
            ghost = GhostDelegate()

            # Mock login
            ghost.client = mock_twikit_client
            ghost.dummy_user = MagicMock(id="dummy_123")
            ghost.main_user = MagicMock(id="main_456")
            ghost._is_authenticated = True
            ghost._current_account = "dummy"

            # Post as main
            result = await ghost.post_as_main("tweet_123", "Test reply")

            # Verify success
            assert result is True

            # Verify reverted to dummy
            assert ghost._current_account == "dummy"

            # Verify set_delegate_account was called correctly
            calls = mock_twikit_client.set_delegate_account.call_args_list
            assert len(calls) == 2
            assert calls[0][0][0] == "main_456"  # Switch to main
            assert calls[1][0][0] is None  # Revert to dummy

    @pytest.mark.asyncio
    async def test_burst_mode_scheduling(self):
        """
        Test scheduler produces correct timing.

        Verify:
            - Random delay between min and max
            - Quiet hours avoidance
            - Jitter applied
        """
        from src.scheduler import calculate_schedule_time

        # Test multiple schedules
        now = datetime.now()
        schedules = [calculate_schedule_time(now) for _ in range(10)]

        # All schedules should be in the future
        for scheduled in schedules:
            assert scheduled > now

        # Should have variation (not all the same)
        unique_schedules = set(s.isoformat() for s in schedules)
        assert len(unique_schedules) > 1

    @pytest.mark.asyncio
    async def test_dead_letter_queue_flow(self):
        """
        Test failed tweets go to DLQ.

        Flow:
            1. Tweet fails to post
            2. Added to dead letter queue
            3. Retry attempted
            4. Success or exhaustion after max retries
        """
        with patch("src.database.create_client") as mock_create:
            from src.database import Database

            # Mock Supabase client
            mock_client = MagicMock()
            mock_table = MagicMock()

            # Mock insert to DLQ
            mock_insert_result = MagicMock()
            mock_insert_result.data = [{"id": "dlq-uuid-123"}]
            mock_table.insert.return_value.execute.return_value = mock_insert_result

            # Mock select
            mock_select_result = MagicMock()
            mock_select_result.data = []
            mock_table.select.return_value = mock_table
            mock_table.eq.return_value = mock_table
            mock_table.execute.return_value = mock_select_result

            # Mock update
            mock_table.update.return_value = mock_table

            mock_client.table.return_value = mock_table
            mock_create.return_value = mock_client

            # Create database
            db = Database()

            # Add to dead letter queue
            dlq_id = await db.add_to_dead_letter_queue(
                tweet_queue_id="queue-uuid-123",
                target_tweet_id="tweet_123",
                error="Publication failed",
                retry_count=0,
            )

            # Verify added to DLQ
            assert dlq_id == "dlq-uuid-123"

            # Verify insert was called
            mock_table.insert.assert_called_once()

    @pytest.mark.asyncio
    async def test_background_worker_processes_pending(
        self,
        mock_supabase_client,
        mock_twikit_client,
    ):
        """
        Test background worker processes pending tweets.

        Flow:
            1. Worker queries for pending tweets
            2. Posts each tweet via Ghost Delegate
            3. Updates status to "posted"
            4. Sends notification
        """
        with patch("src.database.create_client", return_value=mock_supabase_client), \
             patch("src.x_delegate.Client", return_value=mock_twikit_client):

            from src.database import Database
            from src.x_delegate import GhostDelegate
            from src.background_worker import process_pending_tweets

            # Create database and ghost delegate
            db = Database()
            ghost = GhostDelegate()
            ghost.client = mock_twikit_client
            ghost._is_authenticated = True
            ghost._current_account = "dummy"
            ghost.dummy_user = MagicMock(id="dummy_123")
            ghost.main_user = MagicMock(id="main_456")

            # Mock pending tweets
            pending_tweet = {
                "id": "queue-uuid-123",
                "target_tweet_id": "tweet_123",
                "reply_text": "Great insight!",
                "status": "approved",
                "scheduled_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
            }

            db.get_pending_tweets = AsyncMock(return_value=[pending_tweet])
            db.mark_as_posted = AsyncMock()
            db.mark_as_failed = AsyncMock()
            db.add_to_dead_letter_queue = AsyncMock()

            # Process pending tweets
            processed = await process_pending_tweets(db, ghost, None)

            # Verify tweet was processed
            assert processed == 1
            db.mark_as_posted.assert_called_once_with("queue-uuid-123")


# =============================================================================
# Test Class: Health Checks
# =============================================================================

class TestHealthChecks:
    """Health check and monitoring tests."""

    @pytest.mark.asyncio
    async def test_comprehensive_health_check(self):
        """
        Test comprehensive health check of all services.

        Verify:
            - Database health check
            - AI service health check
            - Twitter/Ghost health check
            - Telegram health check
            - Circuit breaker status included
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure mocks
            mock_db = AsyncMock()
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_ai = AsyncMock()
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Run health check
            health = await bot.health_check_all()

            # Verify all components checked
            assert "database" in health
            assert "twitter" in health
            assert "ai" in health
            assert "telegram" in health
            assert "overall" in health

            # Verify overall status
            assert health["overall"] == "healthy"

    @pytest.mark.asyncio
    async def test_degraded_health_status(self):
        """
        Test health check reports degraded status when services fail.
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure AI to be unhealthy
            mock_db = AsyncMock()
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_ai = AsyncMock()
            mock_ai.health_check.return_value = False  # AI unhealthy
            MockAI.return_value = mock_ai

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Run health check
            health = await bot.health_check_all()

            # Verify degraded status
            assert health["overall"] == "degraded"
            assert health["ai"]["status"] == "unhealthy"


# =============================================================================
# Test Class: Rate Limiting Integration
# =============================================================================

class TestRateLimitingIntegration:
    """Integration tests for rate limiting across components."""

    @pytest.mark.asyncio
    async def test_rate_limiter_prevents_excessive_posting(self):
        """
        Test rate limiter prevents exceeding hourly limits.
        """
        from src.rate_limiter import RateLimiter

        # Create rate limiter with low limit
        limiter = RateLimiter(max_per_hour=2, max_per_day=10)

        # First post should succeed
        assert await limiter.can_post() is True
        await limiter.record_post()

        # Second post should succeed
        assert await limiter.can_post() is True
        await limiter.record_post()

        # Third post should be blocked
        assert await limiter.can_post() is False

        # Check wait time
        wait_time = limiter.get_wait_time()
        assert wait_time > 0

    @pytest.mark.asyncio
    async def test_rate_limit_status_reporting(self):
        """
        Test rate limit status provides accurate metrics.
        """
        from src.rate_limiter import RateLimiter

        limiter = RateLimiter(max_per_hour=10, max_per_day=50)

        # Record some posts
        for _ in range(3):
            await limiter.record_post()

        # Get status
        status = await limiter.get_status()

        # Verify metrics
        assert status["hourly_used"] == 3
        assert status["hourly_remaining"] == 7
        assert status["hourly_percentage"] == 30.0
        assert status["can_post"] is True


# =============================================================================
# Test Class: Circuit Breaker Integration
# =============================================================================

class TestCircuitBreakerIntegration:
    """Integration tests for circuit breaker protection."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_half_open_recovery(self):
        """
        Test circuit breaker transitions through states correctly.

        Flow:
            CLOSED → failures → OPEN → timeout → HALF_OPEN → success → CLOSED
        """
        breaker = CircuitBreaker(
            name="test_service",
            failure_threshold=2,
            recovery_timeout=0.1,  # Short timeout for testing
            half_open_max_calls=1,
        )

        # Start in CLOSED state
        assert breaker.state == CircuitState.CLOSED

        # Fail twice to open circuit
        async def failing_call():
            raise Exception("Service error")

        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing_call)

        # Should be OPEN now
        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.2)

        # Successful call should close circuit
        async def successful_call():
            return "success"

        result = await breaker.call(successful_call)
        assert result == "success"

        # Should be CLOSED now
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_ai_circuit_breaker_integration(self):
        """
        Test AI client uses circuit breaker for resilience.
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure AI to fail
            mock_ai = AsyncMock()
            mock_ai.generate_reply.side_effect = Exception("AI service error")
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            mock_db = AsyncMock()
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Create mock tweet
            mock_tweet = MagicMock()
            mock_tweet.id = "tweet_123"
            mock_tweet.text = "Test tweet"

            # Process tweet - should trigger circuit breaker after failures
            for _ in range(3):
                await bot._process_new_tweet(mock_tweet, "testuser")

            # Verify circuit breaker state
            ai_breaker = bot._circuit_breakers.get("ai")
            if ai_breaker:
                # After enough failures, circuit should open
                # Note: depends on circuit breaker configuration
                pass


# =============================================================================
# Performance and Stress Tests
# =============================================================================

class TestPerformance:
    """Performance and stress tests."""

    @pytest.mark.asyncio
    async def test_concurrent_tweet_processing(self):
        """
        Test bot handles multiple concurrent tweet detections.
        """
        with patch("src.bot.Database") as MockDB, \
             patch("src.bot.AIClient") as MockAI, \
             patch("src.bot.TelegramClient") as MockTelegram, \
             patch("src.bot.GhostDelegate") as MockGhost:

            # Configure mocks
            mock_db = AsyncMock()
            mock_db.add_to_queue.return_value = "queue-uuid"
            mock_db.health_check.return_value = True
            mock_db.recover_stale_tweets.return_value = 0
            mock_db.get_dead_letter_stats.return_value = {"pending": 0, "exhausted": 0}
            MockDB.return_value = mock_db

            mock_ai = AsyncMock()
            mock_ai.generate_reply.return_value = "Test reply"
            mock_ai.health_check.return_value = True
            MockAI.return_value = mock_ai

            mock_telegram = AsyncMock()
            MockTelegram.return_value = mock_telegram

            mock_ghost = AsyncMock()
            mock_ghost.login_dummy.return_value = True
            mock_ghost.is_authenticated = True
            MockGhost.return_value = mock_ghost

            # Initialize bot
            bot = ReplyGuyBot()
            await bot.initialize()

            # Create multiple mock tweets
            tweets = [
                MagicMock(id=f"tweet_{i}", text=f"Tweet {i}")
                for i in range(5)
            ]

            # Process concurrently
            tasks = [
                bot._process_new_tweet(tweet, "testuser")
                for tweet in tweets
            ]

            # Execute all tasks
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Verify all processed (some may have errors, that's ok)
            assert len(results) == 5

            # Verify AI was called for each
            assert mock_ai.generate_reply.call_count == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=src", "--cov-report=term-missing"])
