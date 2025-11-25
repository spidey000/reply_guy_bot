"""
Burst Mode Scheduler - Anti-detection timing system.

This module calculates humanized publication times to avoid bot detection.
Instead of posting immediately after approval, tweets are scheduled with
random delays that simulate human behavior.

Anti-Detection Strategies:
    1. Random Delay: 15-120 minutes between approval and publication
    2. Quiet Hours: No posts between 00:00-07:00 (configurable)
    3. Jitter: Add random seconds to avoid exact timestamps

Pattern Generated:
    ┌────────────────────────────────────────────────────────────┐
    │ 00:00-07:00  ░░░░░░░░░░░░░░  Quiet zone (no posts)        │
    │ 07:00-09:00  ▓░░▓░░░░░░░░░░  Morning activity             │
    │ 09:00-12:00  ░░░░░░▓░░░░░░░  Sporadic                     │
    │ 12:00-14:00  ░░▓▓░░░░░░░░░░  Lunch burst                  │
    │ 14:00-18:00  ░░░░░▓░░░░░░░░  Afternoon sporadic           │
    │ 18:00-21:00  ░░▓▓░░░▓░░░░░░  Evening activity             │
    │ 21:00-00:00  ░░░░░░░░░░░░░░  Wind down                    │
    └────────────────────────────────────────────────────────────┘

Configuration (via .env):
    BURST_MODE_ENABLED: Enable/disable the scheduler
    QUIET_HOURS_START: Start of quiet period (0-23)
    QUIET_HOURS_END: End of quiet period (0-23)
    MIN_DELAY_MINUTES: Minimum delay before posting
    MAX_DELAY_MINUTES: Maximum delay before posting
"""

import random
from datetime import datetime, timedelta

from config import settings


def calculate_schedule_time(base_time: datetime | None = None) -> datetime:
    """
    Calculate when to publish the next tweet.

    Applies random delay and avoids quiet hours to simulate human behavior.

    Args:
        base_time: Starting time for calculation. Defaults to now.

    Returns:
        Scheduled publication datetime.

    Example:
        >>> scheduled = calculate_schedule_time()
        >>> print(f"Tweet will be posted at {scheduled}")
    """
    now = base_time or datetime.now()

    # Apply random delay (15-120 minutes by default)
    delay_minutes = random.randint(
        settings.min_delay_minutes,
        settings.max_delay_minutes,
    )
    scheduled = now + timedelta(minutes=delay_minutes)

    # Add jitter to avoid exact timestamps (0-300 seconds)
    jitter_seconds = random.randint(0, 300)
    scheduled += timedelta(seconds=jitter_seconds)

    # Handle quiet hours
    scheduled = _adjust_for_quiet_hours(scheduled)

    return scheduled


def _adjust_for_quiet_hours(scheduled: datetime) -> datetime:
    """
    Adjust scheduled time to avoid quiet hours.

    If the scheduled time falls within quiet hours, move it to
    the end of quiet hours with some randomness.

    Args:
        scheduled: Originally scheduled datetime.

    Returns:
        Adjusted datetime outside of quiet hours.
    """
    quiet_start = settings.quiet_hours_start
    quiet_end = settings.quiet_hours_end

    # Check if scheduled hour is in quiet period
    if quiet_start <= scheduled.hour < quiet_end:
        # Move to end of quiet hours with random minute
        scheduled = scheduled.replace(
            hour=quiet_end,
            minute=random.randint(5, 45),
            second=random.randint(0, 59),
        )

    # Handle case where quiet period spans midnight
    elif quiet_start > quiet_end:
        if scheduled.hour >= quiet_start or scheduled.hour < quiet_end:
            scheduled = scheduled.replace(
                hour=quiet_end,
                minute=random.randint(5, 45),
                second=random.randint(0, 59),
            )
            # If we moved backwards, add a day
            if scheduled < datetime.now():
                scheduled += timedelta(days=1)

    return scheduled


def get_delay_description(scheduled: datetime) -> str:
    """
    Get a human-readable description of the delay.

    Args:
        scheduled: The scheduled datetime.

    Returns:
        Human-readable string like "in 45 minutes" or "at 18:30".
    """
    now = datetime.now()
    delta = scheduled - now

    minutes = int(delta.total_seconds() / 60)

    if minutes < 60:
        return f"in {minutes} minutes"
    elif minutes < 120:
        return f"in 1 hour {minutes - 60} minutes"
    else:
        return f"at {scheduled.strftime('%H:%M')}"
