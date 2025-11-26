"""
Pytest fixtures and configuration.

This module provides shared fixtures for all tests:
- Mock settings for testing without real credentials
- Mock clients for AI, Database, and Twitter
- Sample data generators
- Real functionality test fixtures (time control, in-memory DB, etc.)

Usage:
    def test_something(mock_settings, mock_ai_client):
        # fixtures are automatically injected
        pass
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from freezegun import freeze_time


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers",
        "real: mark test as a real functionality test (not mock-based)"
    )
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test requiring multiple components"
    )
    config.addinivalue_line(
        "markers",
        "slow: mark test as slow (>1s execution time)"
    )


# =============================================================================
# Settings Fixtures
# =============================================================================

@pytest.fixture
def mock_settings():
    """
    Provide mock settings for testing.

    Returns a MagicMock with all required settings attributes.
    """
    settings = MagicMock()

    # Ghost Delegate settings
    settings.dummy_username = "test_dummy"
    settings.dummy_email = "dummy@test.com"
    settings.dummy_password = "test_password"
    settings.main_account_handle = "test_main"
    settings.ghost_delegate_enabled = True
    settings.ghost_delegate_switch_timeout = 30

    # AI settings
    settings.ai_api_key = "test-api-key"
    settings.ai_base_url = "https://api.test.com/v1"
    settings.ai_model = "test-model"

    # Telegram settings
    settings.telegram_bot_token = "test-token"
    settings.telegram_chat_id = "123456789"

    # Supabase settings
    settings.supabase_url = "https://test.supabase.co"
    settings.supabase_key = "test-key"

    # Burst Mode settings
    settings.burst_mode_enabled = True
    settings.quiet_hours_start = 0
    settings.quiet_hours_end = 7
    settings.min_delay_minutes = 15
    settings.max_delay_minutes = 120
    settings.scheduler_check_interval = 60

    # Rate limiter settings
    settings.max_posts_per_hour = 15
    settings.max_posts_per_day = 50
    settings.rate_limit_warning_threshold = 0.8

    return settings


# =============================================================================
# Client Fixtures (Original - for mock tests)
# =============================================================================

@pytest.fixture
def mock_ai_client():
    """Provide mock AI client."""
    client = AsyncMock()
    client.generate_reply.return_value = "This is a test reply!"
    client.health_check.return_value = True
    return client


@pytest.fixture
def mock_database():
    """Provide mock database client."""
    db = AsyncMock()
    db.add_to_queue.return_value = "test-uuid-123"
    db.get_pending_tweets.return_value = []
    db.get_pending_count.return_value = 0
    db.get_posted_today_count.return_value = 0
    db.get_target_accounts.return_value = ["elonmusk", "openai"]
    db.approve_tweet = AsyncMock()
    db.reject_tweet = AsyncMock()
    db.mark_as_posted = AsyncMock()
    db.mark_as_failed = AsyncMock()
    db.add_to_dead_letter_queue = AsyncMock(return_value="mock-dlq-id")
    db.health_check = AsyncMock(return_value=True)
    return db


@pytest.fixture
def mock_ghost_delegate():
    """Provide mock Ghost Delegate."""
    delegate = AsyncMock()
    delegate.login_dummy.return_value = True
    delegate.post_as_main.return_value = True
    delegate.is_authenticated = True
    delegate.validate_session = AsyncMock(return_value=True)
    delegate.get_rate_limit_status = AsyncMock(return_value={
        "hourly_used": 0,
        "hourly_limit": 15,
        "daily_used": 0,
        "daily_limit": 50,
        "can_post": True,
    })
    return delegate


@pytest.fixture
def mock_telegram():
    """Provide mock Telegram client."""
    telegram = AsyncMock()
    telegram.send_approval_request.return_value = 12345
    telegram.send_scheduled_confirmation = AsyncMock()
    telegram.send_published_notification = AsyncMock()
    telegram.send_error_alert = AsyncMock()
    telegram.set_database = Mock()
    telegram.on_approve = Mock()
    telegram.on_reject = Mock()
    return telegram


# =============================================================================
# Data Fixtures (Original)
# =============================================================================

@pytest.fixture
def sample_tweet():
    """Provide sample tweet data."""
    return {
        "id": "1234567890",
        "author": "elonmusk",
        "content": "AI is transforming the world!",
        "created_at": datetime.now().isoformat(),
    }


@pytest.fixture
def sample_queue_item():
    """Provide sample queue item."""
    return {
        "id": "queue-uuid-123",
        "target_tweet_id": "1234567890",
        "target_author": "elonmusk",
        "target_content": "AI is transforming the world!",
        "reply_text": "Absolutely! The potential is incredible.",
        "status": "approved",
        "scheduled_at": datetime.now().isoformat(),
        "posted_at": None,
    }


# =============================================================================
# Time Fixtures (NEW - for real tests)
# =============================================================================

@pytest.fixture
def frozen_time():
    """Freeze time at a specific datetime for testing."""
    with freeze_time("2025-11-26 14:30:00"):
        yield datetime(2025, 11, 26, 14, 30, 0)


@pytest.fixture
def time_controller():
    """
    Provide time control for tests.

    Returns a controller that can freeze and advance time.
    """
    class TimeController:
        def __init__(self):
            self.frozen_datetime = None
            self.freezer = None

        def freeze(self, dt: datetime):
            """Freeze time at specific datetime."""
            if self.freezer:
                self.freezer.stop()
            self.frozen_datetime = dt
            self.freezer = freeze_time(dt)
            self.freezer.start()
            return dt

        def advance(self, **kwargs):
            """Advance frozen time by specified delta."""
            if not self.frozen_datetime:
                raise RuntimeError("Time not frozen")
            self.frozen_datetime += timedelta(**kwargs)
            self.freezer.stop()
            self.freezer = freeze_time(self.frozen_datetime)
            self.freezer.start()
            return self.frozen_datetime

        def stop(self):
            """Unfreeze time."""
            if self.freezer:
                self.freezer.stop()
                self.freezer = None
                self.frozen_datetime = None

    controller = TimeController()
    yield controller
    controller.stop()


# =============================================================================
# Database Fixtures (NEW - for real tests)
# =============================================================================

@pytest.fixture
def in_memory_db_schema() -> str:
    """Return SQL schema for in-memory test database."""
    return """
        -- Tweet queue table
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

        -- Target accounts table
        CREATE TABLE IF NOT EXISTS target_accounts (
            id TEXT PRIMARY KEY,
            handle TEXT NOT NULL UNIQUE,
            enabled INTEGER DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        -- Failed tweets (Dead Letter Queue)
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
    """


@pytest.fixture
def in_memory_db(in_memory_db_schema: str) -> Generator[sqlite3.Connection, None, None]:
    """
    Provide in-memory SQLite database with schema.

    This fixture creates a fresh database for each test.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # Enable dict-like access
    cursor = conn.cursor()

    # Create schema
    cursor.executescript(in_memory_db_schema)
    conn.commit()

    yield conn

    conn.close()


@pytest.fixture
def sample_multiple_tweets():
    """Provide multiple sample tweets with different states."""
    now = datetime.now()
    return [
        {
            "id": "tweet-1",
            "target_tweet_id": "111",
            "target_author": "user1",
            "target_content": "Content 1",
            "reply_text": "Reply 1",
            "status": "pending",
            "scheduled_at": None,
            "posted_at": None,
        },
        {
            "id": "tweet-2",
            "target_tweet_id": "222",
            "target_author": "user2",
            "target_content": "Content 2",
            "reply_text": "Reply 2",
            "status": "approved",
            "scheduled_at": (now - timedelta(minutes=5)).isoformat(),
            "posted_at": None,
        },
        {
            "id": "tweet-3",
            "target_tweet_id": "333",
            "target_author": "user3",
            "target_content": "Content 3",
            "reply_text": "Reply 3",
            "status": "approved",
            "scheduled_at": (now + timedelta(minutes=30)).isoformat(),
            "posted_at": None,
        },
        {
            "id": "tweet-4",
            "target_tweet_id": "444",
            "target_author": "user4",
            "target_content": "Content 4",
            "reply_text": "Reply 4",
            "status": "posted",
            "scheduled_at": (now - timedelta(hours=1)).isoformat(),
            "posted_at": (now - timedelta(minutes=30)).isoformat(),
        },
    ]


# =============================================================================
# Test Function Fixtures (NEW - for real tests)
# =============================================================================

@pytest.fixture
def failing_function():
    """Provide a function that always fails."""
    async def fail():
        raise Exception("Test failure")
    return fail


@pytest.fixture
def succeeding_function():
    """Provide a function that always succeeds."""
    async def succeed():
        return "success"
    return succeed


@pytest.fixture
def intermittent_function():
    """Provide a function that fails N times then succeeds."""
    class IntermittentFunction:
        def __init__(self, fail_count: int = 2):
            self.fail_count = fail_count
            self.calls = 0

        async def __call__(self):
            self.calls += 1
            if self.calls <= self.fail_count:
                raise Exception(f"Failure {self.calls}")
            return "success"

        def reset(self):
            self.calls = 0

    return IntermittentFunction


# =============================================================================
# Mock Response Fixtures (NEW - for real tests)
# =============================================================================

@pytest.fixture
def mock_ai_response():
    """Provide mock AI API response (OpenRouter format)."""
    def create_response(content: str):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        response.raise_for_status = MagicMock()
        return response
    return create_response


# =============================================================================
# Cleanup Fixtures (NEW)
# =============================================================================

@pytest.fixture(autouse=True)
def cleanup_test_files():
    """
    Automatically cleanup test-generated files after each test.

    This fixture runs for every test automatically.
    """
    test_files = [
        "test_cookies.json",
        "test_audit.log",
    ]

    yield

    # Cleanup after test
    for filename in test_files:
        path = Path(filename)
        if path.exists():
            path.unlink()


# =============================================================================
# Assertion Helpers (NEW)
# =============================================================================

@pytest.fixture
def assert_datetime_close():
    """
    Provide helper to assert two datetimes are close.

    Useful for testing time-based calculations with slight variations.
    """
    def _assert_close(dt1: datetime, dt2: datetime, tolerance_seconds: int = 5):
        delta = abs((dt1 - dt2).total_seconds())
        assert delta <= tolerance_seconds, (
            f"Datetimes not within {tolerance_seconds}s: "
            f"{dt1} vs {dt2} (delta: {delta}s)"
        )
    return _assert_close


@pytest.fixture
def assert_status_transition():
    """Provide helper to assert database status transitions."""
    def _assert_transition(
        before_status: str,
        after_status: str,
        expected_sequence: list[str]
    ):
        try:
            before_idx = expected_sequence.index(before_status)
            after_idx = expected_sequence.index(after_status)
            assert after_idx > before_idx, (
                f"Invalid transition: {before_status} → {after_status}. "
                f"Expected sequence: {' → '.join(expected_sequence)}"
            )
        except ValueError as e:
            raise AssertionError(f"Status not in expected sequence: {e}")

    return _assert_transition
