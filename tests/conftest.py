"""
Pytest fixtures and configuration.

This module provides shared fixtures for all tests:
- Mock settings for testing without real credentials
- Mock clients for AI, Database, and Twitter
- Sample data generators

Usage:
    def test_something(mock_settings, mock_ai_client):
        # fixtures are automatically injected
        pass
"""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock


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

    return settings


# =============================================================================
# Client Fixtures
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
    return db


@pytest.fixture
def mock_ghost_delegate():
    """Provide mock Ghost Delegate."""
    delegate = AsyncMock()
    delegate.login_dummy.return_value = True
    delegate.post_as_main.return_value = True
    delegate.is_authenticated = True
    return delegate


@pytest.fixture
def mock_telegram():
    """Provide mock Telegram client."""
    telegram = AsyncMock()
    telegram.send_approval_request.return_value = 12345
    return telegram


# =============================================================================
# Data Fixtures
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
