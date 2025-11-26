"""
Demonstration script for AI retry logic with exponential backoff.

This script shows how the retry mechanism works without requiring actual API calls.
"""

import asyncio
import logging
from unittest.mock import MagicMock, patch
from requests.exceptions import ConnectionError, Timeout

# Configure logging to see retry attempts
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def create_mock_response(content: str):
    """Create a mock requests response."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    response.raise_for_status = MagicMock()
    return response


async def test_successful_after_retries():
    """Test that shows successful generation after 2 failed attempts."""
    print("\n=== Test 1: Successful After 2 Retries ===")

    from src.ai_client import AIClient

    # Create a mock client
    client = AIClient(
        base_url="http://fake-api.com/v1",
        api_key="test-key",
        model="test-model"
    )

    # Mock the API call to fail twice, then succeed
    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            logger.warning(f"Attempt {call_count}: Raising ConnectionError")
            raise ConnectionError("Connection failed")
        logger.warning(f"Attempt {call_count}: Success!")
        return create_mock_response("This is a test reply!")

    with patch("requests.post", side_effect=mock_post):
        result = await client.generate_reply(
            tweet_author="test_user",
            tweet_content="This is a test tweet"
        )

        print(f"Result: {result}")
        print(f"Total attempts: {call_count}")
        assert result == "This is a test reply!"
        print("Test passed - retry logic worked!")


async def test_exhausted_retries():
    """Test that shows graceful failure after exhausting all retries."""
    print("\n=== Test 2: Exhausted Retries (Returns None) ===")

    from src.ai_client import AIClient

    client = AIClient(
        base_url="http://fake-api.com/v1",
        api_key="test-key",
        model="test-model"
    )

    # Mock to always fail with connection error
    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        logger.warning(f"Attempt {call_count}: Raising Timeout")
        raise Timeout("Request timed out")

    with patch("requests.post", side_effect=mock_post):
        result = await client.generate_reply(
            tweet_author="test_user",
            tweet_content="This is a test tweet"
        )

        print(f"Result: {result}")
        print(f"Total attempts: {call_count}")
        assert result is None
        print("Test passed - gracefully returned None after all retries!")


async def test_immediate_success():
    """Test that shows immediate success without retries."""
    print("\n=== Test 3: Immediate Success (No Retries) ===")

    from src.ai_client import AIClient

    client = AIClient(
        base_url="http://fake-api.com/v1",
        api_key="test-key",
        model="test-model"
    )

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        logger.warning(f"Attempt {call_count}: Success!")
        return create_mock_response("Immediate success!")

    with patch("requests.post", side_effect=mock_post):
        result = await client.generate_reply(
            tweet_author="test_user",
            tweet_content="This is a test tweet"
        )

        print(f"Result: {result}")
        print(f"Total attempts: {call_count}")
        assert result == "Immediate success!"
        assert call_count == 1
        print("Test passed - no retries needed!")


async def test_non_retryable_error():
    """Test that non-retryable errors fail immediately without retries."""
    print("\n=== Test 4: Non-Retryable Error (No Retries) ===")

    from src.ai_client import AIClient

    client = AIClient(
        base_url="http://fake-api.com/v1",
        api_key="test-key",
        model="test-model"
    )

    call_count = 0
    def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        logger.warning(f"Attempt {call_count}: Raising ValueError (non-retryable)")
        raise ValueError("Invalid parameter - this should not retry")

    with patch("requests.post", side_effect=mock_post):
        result = await client.generate_reply(
            tweet_author="test_user",
            tweet_content="This is a test tweet"
        )

        print(f"Result: {result}")
        print(f"Total attempts: {call_count}")
        assert result is None
        assert call_count == 1  # Should fail immediately, no retries
        print("Test passed - non-retryable error failed immediately!")


async def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing AI Retry Logic with Exponential Backoff")
    print("=" * 70)

    try:
        await test_immediate_success()
        await test_successful_after_retries()
        await test_exhausted_retries()
        await test_non_retryable_error()

        print("\n" + "=" * 70)
        print("All tests passed! Retry logic is working correctly.")
        print("=" * 70)

    except Exception as e:
        print(f"\nTest failed: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
