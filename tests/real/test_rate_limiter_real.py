"""
Real Functionality Tests - Rate Limiter.

Tests actual rate limiter behavior with real state management:
- Limit enforcement (hourly and daily)
- Recovery after window expiration
- Accurate status reporting
- Sliding window algorithm
- Warning threshold triggering
- Wait time calculations

Mocks: Time (using freezegun)
Real: All rate limiter logic, deque operations, calculations
"""

import asyncio
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from src.rate_limiter import RateLimiter, RateLimitExceeded


@pytest.mark.real
@pytest.mark.asyncio
class TestRateLimiterReal:
    """Real functionality tests for the rate limiter module."""

    async def test_rate_limit_enforcement(self):
        """
        Test that rate limits are properly enforced.

        When the limit is reached, can_post() should return False
        and check_and_record() should raise RateLimitExceeded.
        """
        # Arrange: Create limiter with low limits for testing
        limiter = RateLimiter(max_per_hour=3, max_per_day=10)

        # Act & Assert: First 3 posts should succeed
        for i in range(3):
            can_post = await limiter.can_post()
            assert can_post is True, f"Post {i+1} should be allowed"
            await limiter.record_post()

        # Assert: 4th post should be blocked
        can_post = await limiter.can_post()
        assert can_post is False, "Post 4 should be blocked (hourly limit)"

        # Assert: check_and_record raises exception
        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_and_record()

        assert exc_info.value.limit_type == "hourly"
        assert exc_info.value.wait_time > 0

    async def test_rate_limit_recovery(self):
        """
        Test that rate limits recover after the sliding window expires.

        After 1 hour passes, hourly posts should be cleared from the window
        and new posts should be allowed.
        """
        # Arrange: Use time controller
        start_time = datetime(2025, 11, 26, 10, 0, 0)

        with freeze_time(start_time) as frozen:
            limiter = RateLimiter(max_per_hour=2, max_per_day=10)

            # Fill up hourly limit
            await limiter.record_post()
            await limiter.record_post()

            # Verify blocked
            assert await limiter.can_post() is False

            # Advance time by 61 minutes (beyond 1-hour window)
            frozen.tick(timedelta(minutes=61))

            # Act: Check if posts are now allowed
            can_post = await limiter.can_post()

            # Assert: Should be allowed after window expires
            assert can_post is True, (
                "Posts should be allowed after hourly window expires"
            )

    async def test_get_status_accuracy(self):
        """
        Test that get_status() returns accurate current state.

        Status should reflect actual counts, percentages, and wait times.
        """
        # Arrange
        limiter = RateLimiter(max_per_hour=10, max_per_day=50)

        # Record some posts
        for _ in range(5):
            await limiter.record_post()

        # Act
        status = await limiter.get_status()

        # Assert: Verify all status fields
        assert status["hourly_used"] == 5
        assert status["hourly_limit"] == 10
        assert status["hourly_remaining"] == 5
        assert status["hourly_percentage"] == 50.0

        assert status["daily_used"] == 5
        assert status["daily_limit"] == 50
        assert status["daily_remaining"] == 45
        assert status["daily_percentage"] == 10.0

        assert status["can_post"] is True
        assert status["wait_time_seconds"] == 0

    async def test_sliding_window_behavior(self):
        """
        Test that the sliding window algorithm works correctly.

        Posts should expire individually as they age past the window,
        not all at once.
        """
        start_time = datetime(2025, 11, 26, 10, 0, 0)

        with freeze_time(start_time) as frozen:
            limiter = RateLimiter(max_per_hour=3, max_per_day=100)

            # Record 3 posts at different times
            await limiter.record_post()  # t=0

            frozen.tick(timedelta(minutes=20))
            await limiter.record_post()  # t=20

            frozen.tick(timedelta(minutes=20))
            await limiter.record_post()  # t=40

            # At t=40, all 3 posts are within the hour
            assert await limiter.can_post() is False

            # Advance to t=61 (first post expires)
            frozen.tick(timedelta(minutes=21))

            # Now first post (at t=0) is outside window
            # Only 2 posts remain (at t=20 and t=40)
            assert await limiter.can_post() is True

            await limiter.record_post()  # t=61

            # At t=61, we have posts from t=20, t=40, t=61
            assert await limiter.can_post() is False

            # Advance to t=82 (t=20 post expires)
            frozen.tick(timedelta(minutes=21))

            # Now we have posts from t=40, t=61
            assert await limiter.can_post() is True

    async def test_warning_threshold_triggered(self, caplog):
        """
        Test that warnings are logged when approaching limits.

        At 80% capacity (default threshold), a warning should be logged.
        """
        import logging

        # Arrange
        limiter = RateLimiter(
            max_per_hour=10,
            max_per_day=50,
            warning_threshold=0.8
        )

        # Record posts to reach warning threshold (80%)
        for _ in range(7):
            await limiter.record_post()

        # Act: Check can_post with caplog
        with caplog.at_level(logging.WARNING):
            await limiter.can_post()

        # Assert: Warning should not be triggered yet (70%)
        hourly_warnings = [
            r for r in caplog.records
            if "hourly limit" in r.message.lower()
        ]

        # Record one more to hit 80%
        await limiter.record_post()

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            await limiter.can_post()

        # Assert: Warning should be triggered at 80%
        hourly_warnings = [
            r for r in caplog.records
            if "approaching hourly limit" in r.message.lower()
        ]
        assert len(hourly_warnings) > 0, (
            "Expected warning at 80% capacity"
        )

    async def test_wait_time_calculation(self):
        """
        Test that get_wait_time() calculates correct remaining time.

        Wait time should accurately reflect when the next slot opens.
        """
        start_time = datetime(2025, 11, 26, 10, 0, 0)

        with freeze_time(start_time) as frozen:
            limiter = RateLimiter(max_per_hour=2, max_per_day=100)

            # Record 2 posts
            await limiter.record_post()
            await limiter.record_post()

            # Advance 30 minutes
            frozen.tick(timedelta(minutes=30))

            # Act: Get wait time
            wait_time = limiter.get_wait_time()

            # Assert: Should be ~30 minutes (1800 seconds) until first post expires
            # Allow some tolerance for test execution time
            assert 1750 <= wait_time <= 1850, (
                f"Expected ~1800s wait time, got {wait_time}s"
            )

            # Advance another 35 minutes (total 65 minutes)
            frozen.tick(timedelta(minutes=35))

            # First post now expired, wait time should be 0
            wait_time = limiter.get_wait_time()
            assert wait_time == 0, (
                f"Expected 0 wait time after window expires, got {wait_time}s"
            )

    async def test_daily_limit_separate_from_hourly(self):
        """
        Test that daily and hourly limits are tracked independently.

        Reaching daily limit should block even if hourly has room.
        """
        start_time = datetime(2025, 11, 26, 10, 0, 0)

        with freeze_time(start_time) as frozen:
            # High hourly limit, low daily limit
            limiter = RateLimiter(max_per_hour=100, max_per_day=5)

            # Fill up daily limit across multiple hours
            await limiter.record_post()
            await limiter.record_post()

            frozen.tick(timedelta(hours=2))

            await limiter.record_post()
            await limiter.record_post()
            await limiter.record_post()

            # Assert: Daily limit reached
            assert await limiter.can_post() is False

            status = await limiter.get_status()
            assert status["daily_used"] == 5
            assert status["hourly_used"] == 3  # Only last 3 in current hour

            # Check exception type
            with pytest.raises(RateLimitExceeded) as exc_info:
                await limiter.check_and_record()

            assert exc_info.value.limit_type == "daily"
