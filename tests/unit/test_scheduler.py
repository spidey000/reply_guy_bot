"""
Tests for the Burst Mode Scheduler.

These tests verify that the scheduler correctly:
- Applies delays within configured range
- Respects quiet hours
- Adds appropriate jitter

Run with:
    pytest tests/test_scheduler.py -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch

from src.scheduler import (
    calculate_schedule_time,
    _adjust_for_quiet_hours,
    get_delay_description,
)


class TestCalculateScheduleTime:
    """Tests for calculate_schedule_time function."""

    def test_delay_within_range(self, mock_settings):
        """Scheduled time should be within configured delay range."""
        with patch("src.scheduler.settings", mock_settings):
            base_time = datetime(2024, 1, 15, 12, 0, 0)  # Noon
            scheduled = calculate_schedule_time(base_time)

            min_expected = base_time + timedelta(minutes=15)
            max_expected = base_time + timedelta(minutes=120, seconds=300)

            assert scheduled >= min_expected
            assert scheduled <= max_expected

    def test_returns_datetime(self, mock_settings):
        """Should return a datetime object."""
        with patch("src.scheduler.settings", mock_settings):
            result = calculate_schedule_time()
            assert isinstance(result, datetime)

    def test_scheduled_in_future(self, mock_settings):
        """Scheduled time should always be in the future."""
        with patch("src.scheduler.settings", mock_settings):
            now = datetime.now()
            scheduled = calculate_schedule_time(now)
            assert scheduled > now


class TestQuietHours:
    """Tests for quiet hours adjustment."""

    def test_quiet_hours_avoided(self, mock_settings):
        """Times in quiet hours should be moved outside."""
        with patch("src.scheduler.settings", mock_settings):
            # 3 AM is in quiet hours (0-7)
            quiet_time = datetime(2024, 1, 15, 3, 0, 0)
            adjusted = _adjust_for_quiet_hours(quiet_time)

            assert adjusted.hour >= 7

    def test_normal_hours_unchanged(self, mock_settings):
        """Times outside quiet hours should not be moved."""
        with patch("src.scheduler.settings", mock_settings):
            # 2 PM is outside quiet hours
            normal_time = datetime(2024, 1, 15, 14, 0, 0)
            adjusted = _adjust_for_quiet_hours(normal_time)

            # Hour should remain the same (though minutes may have jitter)
            assert adjusted.hour == 14


class TestDelayDescription:
    """Tests for get_delay_description function."""

    def test_minutes_format(self):
        """Short delays should show minutes."""
        scheduled = datetime.now() + timedelta(minutes=30)
        desc = get_delay_description(scheduled)
        assert "minutes" in desc

    def test_hours_format(self):
        """Longer delays should show hours or time."""
        scheduled = datetime.now() + timedelta(hours=3)
        desc = get_delay_description(scheduled)
        assert "hour" in desc or ":" in desc
