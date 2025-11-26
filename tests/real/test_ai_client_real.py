"""
Real Functionality Tests - AI Client.

Tests actual AI client behavior with focus on retry logic:
- Retry on transient errors
- Retry exhaustion
- Exponential backoff delays
- Successful generation flow
- Reply text cleaning
- Health check

Mocks: OpenAI API client responses
Real: Retry logic, exponential backoff, reply cleaning
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import RateLimitError, APIConnectionError, APITimeoutError

from src.ai_client import AIClient


@pytest.fixture
def ai_client():
    """Create AI client with test configuration."""
    return AIClient(
        base_url="https://api.test.com/v1",
        api_key="test-api-key",
        model="test-model",
    )


@pytest.fixture
def mock_response():
    """Create mock OpenAI response."""
    class MockChoice:
        def __init__(self, content: str):
            self.message = MagicMock()
            self.message.content = content

    class MockResponse:
        def __init__(self, content: str):
            self.choices = [MockChoice(content)]

    return MockResponse


@pytest.mark.real
@pytest.mark.asyncio
class TestAIClientReal:
    """Real functionality tests for the AI client module."""

    async def test_retry_on_transient_error(self, ai_client, mock_response):
        """
        Test that AIClient retries on transient errors.

        Transient errors (RateLimitError, APIConnectionError, APITimeoutError)
        should trigger retry with exponential backoff.
        """
        # Arrange
        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # First two calls fail with transient error
                raise APIConnectionError(request=MagicMock())
            # Third call succeeds
            return mock_response("Success after retry!")

        with patch.object(
            ai_client.client.chat.completions,
            "create",
            side_effect=mock_create
        ):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="testuser",
                tweet_content="Test tweet",
            )

        # Assert
        assert result == "Success after retry!"
        assert call_count == 3

    async def test_retry_exhaustion(self, ai_client):
        """
        Test that AIClient stops retrying after max attempts.

        After exhausting retries, the method should return None
        and log the failure.
        """
        # Arrange
        call_count = 0

        async def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise RateLimitError(
                message="Rate limit exceeded",
                response=MagicMock(status_code=429),
                body={}
            )

        with patch.object(
            ai_client.client.chat.completions,
            "create",
            side_effect=always_fail
        ):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="testuser",
                tweet_content="Test tweet",
            )

        # Assert: Should return None after all retries exhausted
        assert result is None
        assert call_count == 3  # 3 attempts with tenacity

    async def test_exponential_backoff_delays(self, ai_client):
        """
        Test that retry delays follow exponential backoff pattern.

        Delays should be: 1s, 2s, 4s, ... capped at max_delay (8s).
        """
        # Arrange
        delays = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            delays.append(seconds)
            # Don't actually sleep in tests

        call_count = 0

        async def fail_twice(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise APITimeoutError(request=MagicMock())
            return MagicMock(
                choices=[MagicMock(message=MagicMock(content="Success"))]
            )

        with patch("asyncio.sleep", side_effect=mock_sleep):
            with patch.object(
                ai_client.client.chat.completions,
                "create",
                side_effect=fail_twice
            ):
                # Act
                result = await ai_client.generate_reply(
                    tweet_author="testuser",
                    tweet_content="Test tweet",
                )

        # Assert: Delays should follow exponential pattern
        # With tenacity wait_exponential(multiplier=1, min=1, max=8)
        # First delay: 1s, Second delay: 2s
        assert len(delays) == 2
        assert delays[0] >= 1  # At least 1 second
        assert delays[1] >= 2  # At least 2 seconds (exponential)

    async def test_successful_generation(self, ai_client, mock_response):
        """
        Test successful reply generation flow.

        Should return cleaned reply text on first attempt success.
        """
        # Arrange
        expected_reply = "This is a great point! AI is truly transformative."

        async def mock_create(*args, **kwargs):
            return mock_response(expected_reply)

        with patch.object(
            ai_client.client.chat.completions,
            "create",
            side_effect=mock_create
        ):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="elonmusk",
                tweet_content="AI is changing the world",
            )

        # Assert
        assert result == expected_reply

    async def test_reply_cleaning(self, ai_client):
        """
        Test that reply text is properly cleaned.

        The _clean_reply method should:
        - Remove surrounding quotes
        - Truncate to 280 characters
        - Strip whitespace
        """
        # Test 1: Remove double quotes
        result = ai_client._clean_reply('"This is a quoted reply"')
        assert result == "This is a quoted reply"

        # Test 2: Remove single quotes
        result = ai_client._clean_reply("'This is a quoted reply'")
        assert result == "This is a quoted reply"

        # Test 3: Truncate long text
        long_text = "A" * 300
        result = ai_client._clean_reply(long_text)
        assert len(result) == 280
        assert result.endswith("...")

        # Test 4: Strip whitespace
        result = ai_client._clean_reply("  Padded text  ")
        assert result == "Padded text"

        # Test 5: Exactly 280 characters stays unchanged
        exact_text = "A" * 280
        result = ai_client._clean_reply(exact_text)
        assert result == exact_text

    async def test_health_check(self, ai_client):
        """
        Test health check endpoint.

        Should return True when API is accessible, False otherwise.
        """
        # Test 1: Successful health check
        ai_client.client.models.list = AsyncMock(return_value=[])
        result = await ai_client.health_check()
        assert result is True

        # Test 2: Failed health check
        ai_client.client.models.list = AsyncMock(side_effect=Exception("API down"))
        result = await ai_client.health_check()
        assert result is False

    async def test_non_retryable_error(self, ai_client):
        """
        Test that non-retryable errors fail immediately.

        Errors not in (RateLimitError, APIConnectionError, APITimeoutError)
        should not trigger retries.
        """
        # Arrange
        call_count = 0

        async def non_retryable_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid parameter")

        with patch.object(
            ai_client.client.chat.completions,
            "create",
            side_effect=non_retryable_error
        ):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="testuser",
                tweet_content="Test tweet",
            )

        # Assert: Should fail immediately without retries
        assert result is None
        assert call_count == 1  # Only one attempt
