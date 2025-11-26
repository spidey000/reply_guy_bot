"""
Rate Limiter for Twitter API calls.

This module implements a sliding window rate limiter to prevent hitting
Twitter API limits and avoid account bans.

Features:
    - Sliding window algorithm (tracks actual timestamps)
    - Configurable hourly and daily limits
    - In-memory storage (sufficient for MVP)
    - Warning alerts at 80% capacity
    - Thread-safe implementation

Usage:
    rate_limiter = RateLimiter(max_per_hour=15, max_per_day=50)

    if await rate_limiter.can_post():
        # Post tweet
        await rate_limiter.record_post()
    else:
        wait_time = rate_limiter.get_wait_time()
        print(f"Rate limit exceeded. Wait {wait_time}s")
"""

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Deque

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""

    def __init__(self, wait_time: int, limit_type: str = "hourly"):
        self.wait_time = wait_time
        self.limit_type = limit_type
        super().__init__(
            f"Rate limit exceeded ({limit_type}). "
            f"Wait {wait_time} seconds before next post."
        )


class RateLimiter:
    """
    Sliding window rate limiter for Twitter API calls.

    Tracks post timestamps to enforce hourly and daily limits.
    Issues warnings when usage reaches 80% of capacity.
    """

    def __init__(
        self,
        max_per_hour: int = 15,
        max_per_day: int = 50,
        warning_threshold: float = 0.8,
    ):
        """
        Initialize the rate limiter.

        Args:
            max_per_hour: Maximum posts allowed per hour (default: 15)
            max_per_day: Maximum posts allowed per day (default: 50)
            warning_threshold: Percentage to trigger warnings (default: 0.8 = 80%)
        """
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self.warning_threshold = warning_threshold

        # Use deque for efficient FIFO operations
        self.hourly_posts: Deque[datetime] = deque()
        self.daily_posts: Deque[datetime] = deque()

        # Lock for thread-safety
        self._lock = asyncio.Lock()

        logger.info(
            f"Rate limiter initialized: {max_per_hour}/hour, {max_per_day}/day"
        )

    def _clean_old_timestamps(self) -> None:
        """Remove timestamps outside the sliding windows."""
        now = datetime.now()
        one_hour_ago = now - timedelta(hours=1)
        one_day_ago = now - timedelta(days=1)

        # Remove hourly posts older than 1 hour
        while self.hourly_posts and self.hourly_posts[0] < one_hour_ago:
            self.hourly_posts.popleft()

        # Remove daily posts older than 1 day
        while self.daily_posts and self.daily_posts[0] < one_day_ago:
            self.daily_posts.popleft()

    async def can_post(self) -> bool:
        """
        Check if a post can be made within current rate limits.

        Returns:
            True if within limits, False if rate limited.
        """
        async with self._lock:
            self._clean_old_timestamps()

            hourly_count = len(self.hourly_posts)
            daily_count = len(self.daily_posts)

            # Check hourly limit
            if hourly_count >= self.max_per_hour:
                logger.warning(
                    f"Hourly rate limit reached: {hourly_count}/{self.max_per_hour}"
                )
                return False

            # Check daily limit
            if daily_count >= self.max_per_day:
                logger.warning(
                    f"Daily rate limit reached: {daily_count}/{self.max_per_day}"
                )
                return False

            # Warning if approaching limits
            hourly_usage = hourly_count / self.max_per_hour
            daily_usage = daily_count / self.max_per_day

            if hourly_usage >= self.warning_threshold:
                logger.warning(
                    f"Approaching hourly limit: {hourly_count}/{self.max_per_hour} "
                    f"({hourly_usage:.0%})"
                )

            if daily_usage >= self.warning_threshold:
                logger.warning(
                    f"Approaching daily limit: {daily_count}/{self.max_per_day} "
                    f"({daily_usage:.0%})"
                )

            return True

    async def record_post(self) -> None:
        """
        Record a new post timestamp.

        Call this immediately after successfully posting a tweet.
        """
        async with self._lock:
            now = datetime.now()
            self.hourly_posts.append(now)
            self.daily_posts.append(now)

            logger.debug(
                f"Post recorded. Usage: {len(self.hourly_posts)}/{self.max_per_hour} "
                f"hourly, {len(self.daily_posts)}/{self.max_per_day} daily"
            )

    async def get_status(self) -> dict:
        """
        Get current rate limit status.

        Returns:
            Dictionary with usage statistics:
            {
                'hourly_used': int,
                'hourly_limit': int,
                'hourly_remaining': int,
                'hourly_percentage': float,
                'daily_used': int,
                'daily_limit': int,
                'daily_remaining': int,
                'daily_percentage': float,
                'can_post': bool,
                'wait_time_seconds': int,
            }
        """
        async with self._lock:
            self._clean_old_timestamps()

            hourly_used = len(self.hourly_posts)
            daily_used = len(self.daily_posts)

            can_post = (
                hourly_used < self.max_per_hour and
                daily_used < self.max_per_day
            )

            return {
                'hourly_used': hourly_used,
                'hourly_limit': self.max_per_hour,
                'hourly_remaining': max(0, self.max_per_hour - hourly_used),
                'hourly_percentage': (hourly_used / self.max_per_hour) * 100,
                'daily_used': daily_used,
                'daily_limit': self.max_per_day,
                'daily_remaining': max(0, self.max_per_day - daily_used),
                'daily_percentage': (daily_used / self.max_per_day) * 100,
                'can_post': can_post,
                'wait_time_seconds': self.get_wait_time(),
            }

    def get_wait_time(self) -> int:
        """
        Calculate seconds until next available post slot.

        Returns:
            Seconds to wait (0 if can post now).
        """
        self._clean_old_timestamps()

        now = datetime.now()
        wait_times = []

        # Check hourly limit
        if len(self.hourly_posts) >= self.max_per_hour:
            # Wait until oldest hourly post expires
            oldest_hourly = self.hourly_posts[0]
            available_at = oldest_hourly + timedelta(hours=1)
            wait_seconds = int((available_at - now).total_seconds())
            wait_times.append(max(0, wait_seconds))

        # Check daily limit
        if len(self.daily_posts) >= self.max_per_day:
            # Wait until oldest daily post expires
            oldest_daily = self.daily_posts[0]
            available_at = oldest_daily + timedelta(days=1)
            wait_seconds = int((available_at - now).total_seconds())
            wait_times.append(max(0, wait_seconds))

        return max(wait_times) if wait_times else 0

    async def check_and_record(self) -> None:
        """
        Check if can post and record the post atomically.

        Raises:
            RateLimitExceeded: If rate limit is exceeded.
        """
        if not await self.can_post():
            wait_time = self.get_wait_time()

            # Determine which limit was hit
            async with self._lock:
                self._clean_old_timestamps()
                hourly_count = len(self.hourly_posts)
                daily_count = len(self.daily_posts)

                limit_type = "hourly" if hourly_count >= self.max_per_hour else "daily"

            raise RateLimitExceeded(wait_time, limit_type)

        await self.record_post()
