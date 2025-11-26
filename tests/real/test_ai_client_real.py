"""
Real Functionality Tests - AI Client.

Tests actual AI client behavior with focus on retry logic:
- Retry on transient errors
- Retry exhaustion
- Exponential backoff delays
- Successful generation flow
- Reply text cleaning
- Health check

Mocks: requests.post responses
Real: Retry logic, exponential backoff, reply cleaning
"""

import time
from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError, Timeout

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
    """Create mock requests response."""
    def create_response(content: str, status_code: int = 200):
        response = MagicMock()
        response.status_code = status_code
        response.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        response.raise_for_status = MagicMock()
        if status_code >= 400:
            from requests.exceptions import HTTPError
            response.raise_for_status.side_effect = HTTPError()
        return response
    return create_response


@pytest.mark.real
@pytest.mark.asyncio
class TestAIClientReal:
    """Real functionality tests for the AI client module."""

    async def test_retry_on_transient_error(self, ai_client, mock_response):
        """
        Test that AIClient retries on transient errors.

        Transient errors (ConnectionError, Timeout) should trigger
        retry with exponential backoff.
        """
        # Arrange
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # First two calls fail with transient error
                raise ConnectionError("Connection failed")
            # Third call succeeds
            return mock_response("Success after retry!")

        with patch("requests.post", side_effect=mock_post):
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

        def always_fail(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise Timeout("Request timed out")

        with patch("requests.post", side_effect=always_fail):
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
        timestamps = []
        call_count = 0

        def fail_twice(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            timestamps.append(time.time())
            if call_count <= 2:
                raise ConnectionError("Connection failed")
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "Success"}}]
            }
            response.raise_for_status = MagicMock()
            return response

        with patch("requests.post", side_effect=fail_twice):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="testuser",
                tweet_content="Test tweet",
            )

        # Assert: Should have 3 calls with delays between them
        assert len(timestamps) == 3
        # First delay should be at least 1 second
        delay1 = timestamps[1] - timestamps[0]
        assert delay1 >= 0.9  # Allow small variance

    async def test_successful_generation(self, ai_client, mock_response):
        """
        Test successful reply generation flow.

        Should return cleaned reply text on first attempt success.
        """
        # Arrange
        expected_reply = "This is a great point! AI is truly transformative."

        with patch("requests.post", return_value=mock_response(expected_reply)):
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
        success_response = MagicMock()
        success_response.status_code = 200

        with patch("requests.get", return_value=success_response):
            result = await ai_client.health_check()
            assert result is True

        # Test 2: Failed health check
        with patch("requests.get", side_effect=Exception("API down")):
            result = await ai_client.health_check()
            assert result is False

    async def test_non_retryable_error(self, ai_client):
        """
        Test that non-retryable errors fail immediately.

        Errors not in (ConnectionError, Timeout) should not trigger retries.
        """
        # Arrange
        call_count = 0

        def non_retryable_error(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("Invalid parameter")

        with patch("requests.post", side_effect=non_retryable_error):
            # Act
            result = await ai_client.generate_reply(
                tweet_author="testuser",
                tweet_content="Test tweet",
            )

        # Assert: Should fail immediately without retries
        assert result is None
        assert call_count == 1  # Only one attempt
