"""
Tests for RateLimiter class.

This test suite verifies:
- Sliding window rate limiting
- Hourly and daily limits
- Warning thresholds
- Wait time calculations
- Thread safety
"""

import asyncio
from datetime import datetime, timedelta
import pytest

from src.rate_limiter import RateLimiter, RateLimitExceeded


class TestRateLimiter:
    """Test suite for RateLimiter class."""

    @pytest.fixture
    def rate_limiter(self):
        """Create a rate limiter with small limits for testing."""
        return RateLimiter(max_per_hour=5, max_per_day=10, warning_threshold=0.8)

    @pytest.mark.asyncio
    async def test_can_post_within_limits(self, rate_limiter):
        """Test that posting is allowed within limits."""
        assert await rate_limiter.can_post() is True

    @pytest.mark.asyncio
    async def test_hourly_limit_exceeded(self, rate_limiter):
        """Test that hourly limit prevents posting."""
        # Post 5 times (at limit)
        for _ in range(5):
            await rate_limiter.record_post()

        # Should now be rate limited
        assert await rate_limiter.can_post() is False

    @pytest.mark.asyncio
    async def test_daily_limit_exceeded(self, rate_limiter):
        """Test that daily limit prevents posting."""
        # Create limiter with very high hourly limit
        limiter = RateLimiter(max_per_hour=100, max_per_day=3)

        # Post 3 times (at daily limit)
        for _ in range(3):
            await limiter.record_post()

        # Should now be rate limited
        assert await limiter.can_post() is False

    @pytest.mark.asyncio
    async def test_record_post(self, rate_limiter):
        """Test that recording posts updates counters."""
        status_before = await rate_limiter.get_status()
        assert status_before['hourly_used'] == 0
        assert status_before['daily_used'] == 0

        await rate_limiter.record_post()

        status_after = await rate_limiter.get_status()
        assert status_after['hourly_used'] == 1
        assert status_after['daily_used'] == 1

    @pytest.mark.asyncio
    async def test_get_status(self, rate_limiter):
        """Test status reporting."""
        # Post 2 times
        for _ in range(2):
            await rate_limiter.record_post()

        status = await rate_limiter.get_status()

        assert status['hourly_used'] == 2
        assert status['hourly_limit'] == 5
        assert status['hourly_remaining'] == 3
        assert status['hourly_percentage'] == 40.0  # 2/5 = 40%

        assert status['daily_used'] == 2
        assert status['daily_limit'] == 10
        assert status['daily_remaining'] == 8
        assert status['daily_percentage'] == 20.0  # 2/10 = 20%

        assert status['can_post'] is True
        assert status['wait_time_seconds'] == 0

    @pytest.mark.asyncio
    async def test_get_wait_time(self, rate_limiter):
        """Test wait time calculation."""
        # Fill up hourly limit
        for _ in range(5):
            await rate_limiter.record_post()

        wait_time = rate_limiter.get_wait_time()
        assert wait_time > 0
        # Should be close to 1 hour (3600 seconds)
        assert wait_time <= 3600

    @pytest.mark.asyncio
    async def test_sliding_window_cleanup(self, rate_limiter):
        """Test that old timestamps are cleaned up."""
        # Manually add old timestamp
        old_timestamp = datetime.now() - timedelta(hours=2)
        rate_limiter.hourly_posts.append(old_timestamp)

        # Clean should happen automatically
        assert await rate_limiter.can_post() is True

        # Check that old timestamp was removed
        status = await rate_limiter.get_status()
        assert status['hourly_used'] == 0

    @pytest.mark.asyncio
    async def test_warning_threshold(self, rate_limiter, caplog):
        """Test that warnings are logged at 80% capacity."""
        import logging
        caplog.set_level(logging.WARNING)

        # Post 4 times (80% of 5)
        for _ in range(4):
            await rate_limiter.record_post()

        # Next check should trigger warning
        await rate_limiter.can_post()

        # Check that warning was logged
        assert any("Approaching hourly limit" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_check_and_record_success(self):
        """Test atomic check and record operation."""
        limiter = RateLimiter(max_per_hour=2, max_per_day=5)

        # Should succeed
        await limiter.check_and_record()

        status = await limiter.get_status()
        assert status['hourly_used'] == 1

    @pytest.mark.asyncio
    async def test_check_and_record_exceeds_limit(self):
        """Test that check_and_record raises exception when limit exceeded."""
        limiter = RateLimiter(max_per_hour=1, max_per_day=5)

        # First one should succeed
        await limiter.check_and_record()

        # Second should raise exception
        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_and_record()

        assert "hourly" in exc_info.value.limit_type
        assert exc_info.value.wait_time > 0

    @pytest.mark.asyncio
    async def test_concurrent_access(self, rate_limiter):
        """Test thread safety with concurrent operations."""
        async def post_task():
            if await rate_limiter.can_post():
                await rate_limiter.record_post()
                return True
            return False

        # Run 10 concurrent tasks
        results = await asyncio.gather(*[post_task() for _ in range(10)])

        # Should have exactly 5 successes (the hourly limit)
        assert sum(results) == 5

        status = await rate_limiter.get_status()
        assert status['hourly_used'] == 5

    @pytest.mark.asyncio
    async def test_zero_wait_time_when_can_post(self, rate_limiter):
        """Test that wait time is 0 when posting is allowed."""
        wait_time = rate_limiter.get_wait_time()
        assert wait_time == 0

    @pytest.mark.asyncio
    async def test_daily_vs_hourly_limit_precedence(self):
        """Test that the more restrictive limit is enforced."""
        # Daily limit more restrictive
        limiter = RateLimiter(max_per_hour=100, max_per_day=2)

        await limiter.record_post()
        await limiter.record_post()

        assert await limiter.can_post() is False
        status = await limiter.get_status()
        assert status['can_post'] is False

    @pytest.mark.asyncio
    async def test_custom_thresholds(self):
        """Test custom warning thresholds."""
        limiter = RateLimiter(max_per_hour=10, max_per_day=20, warning_threshold=0.5)

        # Post 5 times (50% of hourly)
        for _ in range(5):
            await limiter.record_post()

        # Should still be able to post
        assert await limiter.can_post() is True

    @pytest.mark.asyncio
    async def test_rate_limit_exception_details(self):
        """Test that RateLimitExceeded exception has correct details."""
        limiter = RateLimiter(max_per_hour=1, max_per_day=10)

        await limiter.record_post()

        try:
            await limiter.check_and_record()
            assert False, "Should have raised RateLimitExceeded"
        except RateLimitExceeded as e:
            assert e.wait_time > 0
            assert e.limit_type in ["hourly", "daily"]
            assert "Rate limit exceeded" in str(e)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
