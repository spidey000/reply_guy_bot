"""
Real Functionality Tests - Scheduler.

Tests actual scheduler logic with deterministic outputs:
- calculate_schedule_time() returns future times
- Quiet hours are properly enforced
- Jitter is applied within expected range
- Midnight-spanning quiet periods handled
- Delay descriptions are accurate

Mocks: Configuration settings (optional)
Real: All scheduler functions, datetime calculations
"""

import random
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from freezegun import freeze_time

# Import the module under test
from src.scheduler import (
    calculate_schedule_time,
    get_delay_description,
    _adjust_for_quiet_hours,
)


@pytest.mark.real
class TestSchedulerReal:
    """Real functionality tests for the scheduler module."""

    def test_calculate_schedule_time_returns_future_time(self):
        """
        Test that calculate_schedule_time() always returns a future datetime.

        This verifies the core scheduling invariant: scheduled time > current time.
        The delay should be at least MIN_DELAY_MINUTES in the future.
        """
        # Arrange: Fix the current time
        test_time = datetime(2025, 11, 26, 14, 30, 0)

        with freeze_time(test_time):
            # Act: Generate multiple scheduled times
            scheduled_times = [calculate_schedule_time() for _ in range(20)]

        # Assert: All scheduled times are in the future
        for scheduled in scheduled_times:
            assert scheduled > test_time, (
                f"Scheduled time {scheduled} should be after {test_time}"
            )

        # Assert: All scheduled times are at least MIN_DELAY_MINUTES in future
        # Default min delay is 15 minutes
        min_expected = test_time + timedelta(minutes=15)
        for scheduled in scheduled_times:
            assert scheduled >= min_expected, (
                f"Scheduled time {scheduled} should be at least {min_expected}"
            )

    def test_quiet_hours_respected(self):
        """
        Test that scheduled times avoid quiet hours (00:00-07:00 by default).

        When scheduling during active hours, if the calculated time falls
        into quiet hours, it should be moved to after quiet hours end.
        """
        # Arrange: Set time close to quiet hours start
        # If we schedule at 23:00 with max delay 120min, it might fall into quiet hours
        test_time = datetime(2025, 11, 26, 23, 30, 0)

        with freeze_time(test_time):
            # Mock settings for predictable quiet hours
            with patch("src.scheduler.settings") as mock_settings:
                mock_settings.quiet_hours_start = 0
                mock_settings.quiet_hours_end = 7
                mock_settings.min_delay_minutes = 15
                mock_settings.max_delay_minutes = 120

                # Act: Generate multiple scheduled times
                # Some should theoretically fall into quiet hours without adjustment
                scheduled_times = [calculate_schedule_time() for _ in range(50)]

        # Assert: No scheduled times are during quiet hours (00:00-07:00)
        for scheduled in scheduled_times:
            hour = scheduled.hour
            # Quiet hours are 0-7, so hour should be >= 7
            assert hour >= 7 or hour < 0, (
                f"Scheduled hour {hour}:xx falls within quiet hours (0-7)"
            )

    def test_jitter_applied(self):
        """
        Test that random jitter is applied to scheduled times.

        Jitter should add 0-300 seconds of randomness to avoid
        exact timestamp patterns that could indicate bot behavior.
        """
        # Arrange: Fix both time and random seed for deterministic test
        test_time = datetime(2025, 11, 26, 10, 0, 0)

        with freeze_time(test_time):
            with patch("src.scheduler.settings") as mock_settings:
                mock_settings.quiet_hours_start = 0
                mock_settings.quiet_hours_end = 7
                mock_settings.min_delay_minutes = 30
                mock_settings.max_delay_minutes = 30  # Fixed delay

                # Act: Generate multiple scheduled times with same delay
                scheduled_times = []
                for _ in range(20):
                    scheduled = calculate_schedule_time()
                    scheduled_times.append(scheduled)

        # Assert: Times should have variation due to jitter (0-300 seconds)
        # Extract seconds from each scheduled time
        seconds_values = [s.second for s in scheduled_times]

        # With jitter, we should see variation in seconds
        unique_seconds = set(seconds_values)
        assert len(unique_seconds) > 1, (
            "Expected variation in seconds due to jitter, but all times had same seconds"
        )

        # Calculate actual minute offsets from base time
        offsets_seconds = [
            (s - test_time).total_seconds() for s in scheduled_times
        ]

        # All should be within 30 minutes + 300 seconds of jitter
        min_offset = 30 * 60  # 30 minutes in seconds
        max_offset = 30 * 60 + 300  # 30 minutes + max jitter

        for offset in offsets_seconds:
            assert min_offset <= offset <= max_offset, (
                f"Offset {offset}s not in expected range [{min_offset}, {max_offset}]"
            )

    def test_quiet_hours_spanning_midnight(self):
        """
        Test handling of quiet hours that span midnight (e.g., 22:00-06:00).

        When quiet_hours_start > quiet_hours_end, the quiet period wraps
        around midnight and should be handled correctly.
        """
        # Arrange: Set time during midnight-spanning quiet hours
        test_time = datetime(2025, 11, 26, 23, 30, 0)

        with patch("src.scheduler.settings") as mock_settings:
            # Quiet hours from 22:00 to 06:00 (spans midnight)
            mock_settings.quiet_hours_start = 22
            mock_settings.quiet_hours_end = 6

            # Test _adjust_for_quiet_hours directly
            # Time at 23:30 should be moved to 06:xx next day
            input_time = datetime(2025, 11, 26, 23, 30, 0)

            # Act
            adjusted = _adjust_for_quiet_hours(input_time)

            # Assert: Should be moved to after 6 AM
            assert adjusted.hour >= 6, (
                f"Time {adjusted} should be adjusted to after 06:00"
            )

            # Test time at 02:00 (also in quiet period)
            input_time_2 = datetime(2025, 11, 27, 2, 0, 0)
            adjusted_2 = _adjust_for_quiet_hours(input_time_2)

            assert adjusted_2.hour >= 6, (
                f"Time {adjusted_2} at 02:00 should be adjusted to after 06:00"
            )

    def test_delay_description_accuracy(self):
        """
        Test that get_delay_description() returns accurate human-readable strings.

        The description should match the actual delay:
        - Under 60 min: "in X minutes"
        - 60-120 min: "in 1 hour X minutes"
        - Over 120 min: "at HH:MM"
        """
        # Test case 1: Under 60 minutes
        with freeze_time("2025-11-26 14:00:00"):
            scheduled_45min = datetime(2025, 11, 26, 14, 45, 0)
            desc = get_delay_description(scheduled_45min)

            assert "45 minutes" in desc, (
                f"Expected '45 minutes' in description, got: {desc}"
            )

        # Test case 2: Between 60-120 minutes
        with freeze_time("2025-11-26 14:00:00"):
            scheduled_90min = datetime(2025, 11, 26, 15, 30, 0)
            desc = get_delay_description(scheduled_90min)

            assert "1 hour" in desc and "30 minutes" in desc, (
                f"Expected '1 hour 30 minutes' in description, got: {desc}"
            )

        # Test case 3: Over 120 minutes
        with freeze_time("2025-11-26 14:00:00"):
            scheduled_3hr = datetime(2025, 11, 26, 17, 30, 0)
            desc = get_delay_description(scheduled_3hr)

            assert "17:30" in desc, (
                f"Expected '17:30' in description for >2hr delay, got: {desc}"
            )

        # Test case 4: Edge case - exactly 60 minutes
        with freeze_time("2025-11-26 14:00:00"):
            scheduled_60min = datetime(2025, 11, 26, 15, 0, 0)
            desc = get_delay_description(scheduled_60min)

            # Should be "in 1 hour 0 minutes"
            assert "1 hour" in desc, (
                f"Expected '1 hour' in description for 60min delay, got: {desc}"
            )
