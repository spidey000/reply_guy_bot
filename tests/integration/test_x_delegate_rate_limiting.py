"""
Integration tests for RateLimiter with GhostDelegate.

This test suite verifies:
- Rate limiter is properly initialized
- Rate limiting prevents excessive posting
- Rate limit status is correctly reported

Note: This test focuses on the RateLimiter integration without requiring
full environment setup for GhostDelegate.
"""

import pytest
from src.rate_limiter import RateLimiter


class TestRateLimiterIntegration:
    """Integration tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_realistic_hourly_scenario(self):
        """Test realistic posting scenario with hourly limits."""
        limiter = RateLimiter(max_per_hour=15, max_per_day=50)

        # Post 14 times (within limit)
        for i in range(14):
            assert await limiter.can_post()
            await limiter.record_post()

        # 15th post should succeed
        assert await limiter.can_post()
        await limiter.record_post()

        # 16th post should be blocked
        assert not await limiter.can_post()

        status = await limiter.get_status()
        assert status['hourly_used'] == 15
        assert status['can_post'] is False

    @pytest.mark.asyncio
    async def test_realistic_daily_scenario(self):
        """Test realistic posting scenario with daily limits."""
        limiter = RateLimiter(max_per_hour=100, max_per_day=50)

        # Post 49 times (within limit)
        for i in range(49):
            assert await limiter.can_post()
            await limiter.record_post()

        # 50th post should succeed
        assert await limiter.can_post()
        await limiter.record_post()

        # 51st post should be blocked
        assert not await limiter.can_post()

        status = await limiter.get_status()
        assert status['daily_used'] == 50
        assert status['can_post'] is False

    @pytest.mark.asyncio
    async def test_status_reporting_at_80_percent(self):
        """Test warning at 80% threshold."""
        limiter = RateLimiter(max_per_hour=10, max_per_day=20, warning_threshold=0.8)

        # Post 7 times (70%)
        for i in range(7):
            await limiter.record_post()

        status = await limiter.get_status()
        assert status['hourly_percentage'] == 70.0
        assert status['can_post'] is True

        # Post 8th time (80%)
        await limiter.record_post()

        status = await limiter.get_status()
        assert status['hourly_percentage'] == 80.0
        assert status['can_post'] is True

    @pytest.mark.asyncio
    async def test_wait_time_accuracy(self):
        """Test wait time calculation accuracy."""
        limiter = RateLimiter(max_per_hour=2, max_per_day=10)

        # Fill hourly limit
        await limiter.record_post()
        await limiter.record_post()

        # Should be rate limited
        assert not await limiter.can_post()

        # Wait time should be around 3600 seconds (1 hour)
        wait_time = limiter.get_wait_time()
        assert 0 < wait_time <= 3600

    @pytest.mark.asyncio
    async def test_multiple_limits_interaction(self):
        """Test that both hourly and daily limits work together."""
        limiter = RateLimiter(max_per_hour=5, max_per_day=10)

        # Post 10 times across "multiple hours" (simulated)
        for i in range(10):
            # For this test, we'll just post 10 times
            # In reality, timestamps would spread across hours
            await limiter.record_post()

        # Now we're at daily limit
        assert not await limiter.can_post()

        status = await limiter.get_status()
        assert status['daily_used'] == 10
        assert status['daily_remaining'] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
