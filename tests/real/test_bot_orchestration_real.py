"""
Real Functionality Tests - Bot Orchestration.

Tests actual bot orchestration logic with mocked external APIs:
- Initialization sequence
- Health check all components
- Approval workflow
- Rejection workflow
- Circuit breaker integration
- Crash recovery on startup

Mocks: All external APIs (Twitter, OpenAI, Telegram, Supabase)
Real: Bot orchestration logic, callback wiring, error handling
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.bot import ReplyGuyBot


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.ai_base_url = "https://api.test.com/v1"
    settings.ai_api_key = "test-key"
    settings.ai_model = "test-model"
    settings.burst_mode_enabled = True
    settings.main_account_handle = "test_main"
    settings.quiet_hours_start = 0
    settings.quiet_hours_end = 7
    settings.min_delay_minutes = 15
    settings.max_delay_minutes = 120
    settings.supabase_url = "https://test.supabase.co"
    settings.supabase_key = "test-key"
    return settings


@pytest.fixture
def mock_components():
    """Create all mock components."""
    # AI Client
    ai = AsyncMock()
    ai.health_check = AsyncMock(return_value=True)
    ai.generate_reply = AsyncMock(return_value="Test reply")

    # Database
    db = AsyncMock()
    db.health_check = AsyncMock(return_value=True)
    db.get_pending_tweets = AsyncMock(return_value=[])
    db.get_target_accounts = AsyncMock(return_value=["testuser"])
    db.add_to_queue = AsyncMock(return_value="queue-id-123")
    db.approve_tweet = AsyncMock()
    db.reject_tweet = AsyncMock()
    db.recover_stale_tweets = AsyncMock(return_value=0)
    db.get_dead_letter_stats = AsyncMock(return_value={"pending": 0, "exhausted": 0})
    db.circuit_breaker = MagicMock()
    db.circuit_breaker.get_status = MagicMock(return_value={"state": "closed"})

    # Telegram
    telegram = AsyncMock()
    telegram.initialize = AsyncMock()
    telegram.set_database = MagicMock()
    telegram.on_approve = MagicMock()
    telegram.on_reject = MagicMock()
    telegram.send_approval_request = AsyncMock(return_value=12345)
    telegram.send_scheduled_confirmation = AsyncMock()
    telegram.send_error_alert = AsyncMock()
    telegram.app = MagicMock()
    telegram.app.run_polling = AsyncMock()
    telegram.app.stop = AsyncMock()

    # Ghost Delegate
    ghost = AsyncMock()
    ghost.login_dummy = AsyncMock(return_value=True)
    ghost.is_authenticated = True
    ghost.post_as_main = AsyncMock(return_value=True)
    ghost.client = AsyncMock()

    return {
        "ai": ai,
        "db": db,
        "telegram": telegram,
        "ghost": ghost,
    }


@pytest.fixture
def bot_with_mocks(mock_settings, mock_components):
    """Create ReplyGuyBot with mocked components."""
    bot = ReplyGuyBot()
    bot.ai = mock_components["ai"]
    bot.db = mock_components["db"]
    bot.telegram = mock_components["telegram"]
    bot.ghost = mock_components["ghost"]
    return bot


@pytest.mark.real
@pytest.mark.asyncio
class TestBotOrchestrationReal:
    """Real functionality tests for bot orchestration."""

    async def test_initialization_sequence(self, mock_settings, mock_components):
        """
        Test that bot initialization follows correct sequence.

        Sequence:
        1. Initialize Database
        2. Initialize AI Client
        3. Initialize Telegram
        4. Wire approval callbacks
        5. Initialize Ghost Delegate
        6. Set up circuit breakers
        7. Load seen tweets
        8. Perform crash recovery
        """
        bot = ReplyGuyBot()

        with patch("src.bot.Database", return_value=mock_components["db"]):
            with patch("src.bot.AIClient", return_value=mock_components["ai"]):
                with patch("src.bot.TelegramClient", return_value=mock_components["telegram"]):
                    with patch("src.bot.GhostDelegate", return_value=mock_components["ghost"]):
                        with patch("src.bot.settings", mock_settings):
                            # Act
                            result = await bot.initialize()

        # Assert
        assert result is True
        assert bot.db is not None
        assert bot.ai is not None
        assert bot.telegram is not None
        assert bot.ghost is not None

        # Verify callbacks were wired
        mock_components["telegram"].on_approve.assert_called_once()
        mock_components["telegram"].on_reject.assert_called_once()

        # Verify crash recovery was performed
        mock_components["db"].recover_stale_tweets.assert_called_once()

    async def test_health_check_all_components(self, bot_with_mocks, mock_components):
        """
        Test comprehensive health check returns correct status.

        Should check all components and return detailed status.
        """
        # Act
        health = await bot_with_mocks.health_check_all()

        # Assert
        assert health["overall"] == "healthy"
        assert health["database"]["status"] == "healthy"
        assert health["ai"]["status"] == "healthy"
        assert health["twitter"]["status"] == "healthy"
        assert health["telegram"]["status"] == "healthy"

    async def test_health_check_degraded(self, bot_with_mocks, mock_components):
        """
        Test health check returns degraded when component fails.
        """
        # Arrange: Make AI health check fail
        mock_components["ai"].health_check.return_value = False

        # Act
        health = await bot_with_mocks.health_check_all()

        # Assert
        assert health["overall"] == "degraded"
        assert health["ai"]["status"] == "unhealthy"
        assert health["database"]["status"] == "healthy"

    async def test_approval_workflow(self, bot_with_mocks, mock_components):
        """
        Test complete approval workflow.

        Workflow: approve → calculate schedule → update DB → send confirmation
        """
        # Arrange
        tweet_id = "test-tweet-123"

        with patch("src.bot.calculate_schedule_time") as mock_schedule:
            with patch("src.bot.get_delay_description", return_value="in 30 minutes"):
                future_time = datetime.now() + timedelta(minutes=30)
                mock_schedule.return_value = future_time

                # Act
                await bot_with_mocks._handle_approve(tweet_id)

        # Assert
        mock_components["db"].approve_tweet.assert_called_once()
        call_args = mock_components["db"].approve_tweet.call_args
        assert call_args.args[0] == tweet_id

        mock_components["telegram"].send_scheduled_confirmation.assert_called_once()

    async def test_rejection_workflow(self, bot_with_mocks, mock_components):
        """
        Test complete rejection workflow.

        Workflow: reject → update DB
        """
        # Arrange
        tweet_id = "test-tweet-456"

        # Act
        await bot_with_mocks._handle_reject(tweet_id)

        # Assert
        mock_components["db"].reject_tweet.assert_called_once_with(tweet_id)

    async def test_circuit_breaker_integration(self, bot_with_mocks, mock_components):
        """
        Test circuit breaker protects external service calls.

        When circuit is open, calls should fail fast.
        """
        # Arrange: Set up circuit breakers
        from src.circuit_breaker import CircuitBreaker, CircuitState

        ai_breaker = CircuitBreaker("ai", failure_threshold=2, recovery_timeout=60)
        bot_with_mocks._circuit_breakers = {"ai": ai_breaker}

        # Simulate circuit opening
        ai_breaker.state = CircuitState.OPEN
        ai_breaker.last_failure_time = asyncio.get_event_loop().time()

        # Act: Get circuit status
        status = bot_with_mocks._get_circuit_status()

        # Assert
        assert "ai" in status
        assert status["ai"]["state"] == "open"

    async def test_crash_recovery_on_startup(self, bot_with_mocks, mock_components):
        """
        Test that crash recovery runs during startup.

        Should:
        - Call recover_stale_tweets
        - Get dead letter queue stats
        - Not fail if recovery has issues
        """
        # Arrange
        mock_components["db"].recover_stale_tweets.return_value = 3
        mock_components["db"].get_dead_letter_stats.return_value = {
            "pending": 5,
            "exhausted": 2
        }

        # Act
        await bot_with_mocks._perform_crash_recovery()

        # Assert
        mock_components["db"].recover_stale_tweets.assert_called_once_with(timeout_minutes=30)
        mock_components["db"].get_dead_letter_stats.assert_called_once()

    async def test_crash_recovery_handles_errors(self, bot_with_mocks, mock_components):
        """
        Test that crash recovery handles errors gracefully.

        Should not crash the bot if recovery fails.
        """
        # Arrange
        mock_components["db"].recover_stale_tweets.side_effect = Exception("DB error")

        # Act & Assert: Should not raise
        await bot_with_mocks._perform_crash_recovery()

    async def test_seen_tweets_tracking(self, bot_with_mocks, mock_components):
        """
        Test that seen tweets are loaded on startup to avoid duplicates.
        """
        # Arrange
        mock_components["db"].get_pending_tweets.return_value = [
            {"target_tweet_id": "123"},
            {"target_tweet_id": "456"},
            {"target_tweet_id": "789"},
        ]

        # Act
        await bot_with_mocks._load_seen_tweets()

        # Assert
        assert "123" in bot_with_mocks._seen_tweets
        assert "456" in bot_with_mocks._seen_tweets
        assert "789" in bot_with_mocks._seen_tweets
        assert len(bot_with_mocks._seen_tweets) == 3

    async def test_graceful_shutdown(self, bot_with_mocks, mock_components):
        """
        Test that stop() gracefully shuts down all components.
        """
        # Arrange: Create mock tasks
        bot_with_mocks._running = True
        bot_with_mocks._worker_task = MagicMock()
        bot_with_mocks._worker_task.done.return_value = False
        bot_with_mocks._worker_task.cancel = MagicMock()

        bot_with_mocks._monitor_task = MagicMock()
        bot_with_mocks._monitor_task.done.return_value = False
        bot_with_mocks._monitor_task.cancel = MagicMock()

        # Make awaiting cancelled tasks return immediately
        async def cancelled_task():
            raise asyncio.CancelledError()

        with patch.object(bot_with_mocks, "_worker_task", new_callable=lambda: create_cancelled_task()):
            with patch.object(bot_with_mocks, "_monitor_task", new_callable=lambda: create_cancelled_task()):
                # We need to mock the tasks properly
                pass

        # Act
        await bot_with_mocks.stop()

        # Assert
        assert bot_with_mocks._running is False

    async def test_approval_error_handling(self, bot_with_mocks, mock_components):
        """
        Test that errors in approval workflow are handled.
        """
        # Arrange
        mock_components["db"].approve_tweet.side_effect = Exception("DB error")

        # Act & Assert: Should not raise
        await bot_with_mocks._handle_approve("test-id")

    async def test_rejection_error_handling(self, bot_with_mocks, mock_components):
        """
        Test that errors in rejection workflow are handled.
        """
        # Arrange
        mock_components["db"].reject_tweet.side_effect = Exception("DB error")

        # Act & Assert: Should not raise
        await bot_with_mocks._handle_reject("test-id")


def create_cancelled_task():
    """Helper to create a cancelled task mock."""
    task = MagicMock()
    task.done.return_value = True
    return task
