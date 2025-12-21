import pytest
from unittest.mock import AsyncMock, MagicMock
from src.telegram_client import TelegramClient
from config import settings

@pytest.mark.asyncio
async def test_send_startup_notification():
    # Mock settings
    settings.telegram_bot_token = "fake_token"
    settings.telegram_chat_id = "fake_chat_id"
    settings.main_account_handle = "test_bot"
    settings.burst_mode_enabled = True

    client = TelegramClient()
    client.app = MagicMock()
    client.app.bot.send_message = AsyncMock()

    await client.send_startup_notification()

    client.app.bot.send_message.assert_called_once()
    args, kwargs = client.app.bot.send_message.call_args
    assert kwargs['chat_id'] == "fake_chat_id"
    assert "🚀 *Bot Started*" in kwargs['text']
    assert "@test_bot" in kwargs['text']
    assert "Burst" in kwargs['text']

@pytest.mark.asyncio
async def test_send_stop_notification():
    # Mock settings
    settings.telegram_chat_id = "fake_chat_id"

    client = TelegramClient()
    client.app = MagicMock()
    client.app.bot.send_message = AsyncMock()

    await client.send_stop_notification(reason="Test Stop")

    client.app.bot.send_message.assert_called_once()
    args, kwargs = client.app.bot.send_message.call_args
    assert kwargs['chat_id'] == "fake_chat_id"
    assert "🛑 *Bot Stopped*" in kwargs['text']
    assert "Test Stop" in kwargs['text']

@pytest.mark.asyncio
async def test_send_error_alert_publication_failure():
    # Mock settings
    settings.telegram_chat_id = "fake_chat_id"

    client = TelegramClient()
    client.app = MagicMock()
    client.app.bot.send_message = AsyncMock()

    await client.send_error_alert(
        error_type="publication_failed",
        message="Failed to publish reply for tweet 123",
        details={"tweet_id": "queue_456", "error": "API Error"}
    )

    client.app.bot.send_message.assert_called_once()
    args, kwargs = client.app.bot.send_message.call_args
    assert "🚨 *CRITICAL ALERT*" in kwargs['text']
    assert "publication_failed" in kwargs['text']
    assert "Failed to publish reply for tweet 123" in kwargs['text']
    assert "queue_456" in kwargs['text']
    assert "API Error" in kwargs['text']
