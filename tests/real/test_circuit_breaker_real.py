"""
Real Functionality Tests - Circuit Breaker.

Tests actual circuit breaker state machine behavior:
- CLOSED → OPEN transition on failures
- OPEN → HALF_OPEN transition on timeout
- HALF_OPEN → CLOSED on success
- HALF_OPEN → OPEN on failure
- Accurate status reporting
- Half-open call limiting
- Backoff decorator retry logic
- Exponential backoff delay calculations

Mocks: Time, test functions (failing/succeeding)
Real: Circuit breaker state machine, all transitions, backoff calculations
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import patch, AsyncMock

import pytest
from freezegun import freeze_time

from src.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    CircuitBreakerError,
    with_backoff,
)


@pytest.mark.real
class TestCircuitBreakerReal:
    """Real functionality tests for the circuit breaker module."""

    @pytest.mark.asyncio
    async def test_closed_to_open_transition(self):
        """
        Test that circuit transitions from CLOSED to OPEN after failure threshold.

        When consecutive failures reach the threshold, the circuit should open.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold=3,
            recovery_timeout=60,
        )

        async def failing_func():
            raise Exception("Test failure")

        # Assert: Initial state is CLOSED
        assert breaker.state == CircuitState.CLOSED

        # Act: Trigger failures up to threshold
        for i in range(3):
            with pytest.raises(Exception, match="Test failure"):
                await breaker.call(failing_func)

        # Assert: Circuit should now be OPEN
        assert breaker.state == CircuitState.OPEN
        assert breaker.failures >= 3

    @pytest.mark.asyncio
    async def test_open_to_half_open_transition(self):
        """
        Test that circuit transitions from OPEN to HALF_OPEN after recovery timeout.

        After the recovery timeout elapses, the next call attempt should
        move the circuit to HALF_OPEN state.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold=2,
            recovery_timeout=5,  # Short timeout for testing
        )

        async def failing_func():
            raise Exception("Test failure")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing_func)

        assert breaker.state == CircuitState.OPEN

        # Mock time to advance past recovery timeout
        original_time = time.time
        with patch("time.time") as mock_time:
            mock_time.return_value = original_time() + 10  # 10s after last failure

            # Act: Attempt to check if call is allowed
            can_attempt = breaker._can_attempt()

            # Assert: Should transition to HALF_OPEN
            assert can_attempt is True
            assert breaker.state == CircuitState.HALF_OPEN

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self):
        """
        Test that successful call in HALF_OPEN state closes the circuit.

        A success during the recovery test phase should reset the circuit.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold=2,
            recovery_timeout=1,
        )

        call_count = 0

        async def intermittent_func():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("Temporary failure")
            return "success"

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(intermittent_func)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Act: Call should succeed and close circuit
        result = await breaker.call(intermittent_func)

        # Assert
        assert result == "success"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        """
        Test that failure in HALF_OPEN state reopens the circuit.

        A failure during recovery should send the circuit back to OPEN.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold=2,
            recovery_timeout=1,
        )

        async def always_failing():
            raise Exception("Always fails")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(always_failing)

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Verify we're in HALF_OPEN
        breaker._can_attempt()
        assert breaker.state == CircuitState.HALF_OPEN

        # Act: Failure during HALF_OPEN
        with pytest.raises(Exception):
            await breaker.call(always_failing)

        # Assert: Should reopen
        assert breaker.state == CircuitState.OPEN

    def test_get_status_accuracy(self):
        """
        Test that get_status() returns accurate state information.

        Status should correctly reflect current state and metrics.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_status_breaker",
            failure_threshold=5,
            recovery_timeout=60,
        )

        # Act: Initial status
        status = breaker.get_status()

        # Assert: Initial state
        assert status["name"] == "test_status_breaker"
        assert status["state"] == "closed"
        assert status["failures"] == 0
        assert status["successes"] == 0
        assert status["failure_threshold"] == 5
        assert status["wait_time_seconds"] == 0.0

        # Record some activity
        breaker.record_success()
        breaker.record_success()
        breaker.record_failure()

        status = breaker.get_status()
        assert status["successes"] == 2
        assert status["failures"] == 1

    @pytest.mark.asyncio
    async def test_half_open_call_limiting(self):
        """
        Test that HALF_OPEN state limits the number of test calls.

        Only half_open_max_calls should be allowed before blocking.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="test_breaker",
            failure_threshold=2,
            recovery_timeout=1,
            half_open_max_calls=2,
        )

        async def failing():
            raise Exception("Fail")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing)

        # Wait and enter HALF_OPEN
        await asyncio.sleep(1.1)
        breaker._can_attempt()
        assert breaker.state == CircuitState.HALF_OPEN

        # Act: Try more calls than allowed in HALF_OPEN
        # First 2 calls should be allowed
        assert breaker._can_attempt() is True  # Call 1 (already counted above)
        assert breaker._can_attempt() is True  # Call 2

        # Third call should be blocked
        assert breaker._can_attempt() is False

    @pytest.mark.asyncio
    async def test_backoff_decorator_retry_logic(self):
        """
        Test that the with_backoff decorator retries on failure.

        The decorator should retry the specified number of times
        before raising the final exception.
        """
        # Arrange
        call_count = 0

        @with_backoff(
            max_retries=3,
            base_delay=0.01,  # Very short for testing
            max_delay=0.1,
            exceptions=(ValueError,),
        )
        async def intermittent_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Temporary error")
            return "success"

        # Act
        result = await intermittent_func()

        # Assert
        assert result == "success"
        assert call_count == 3  # Failed twice, succeeded on third

    @pytest.mark.asyncio
    async def test_backoff_exponential_delays(self):
        """
        Test that backoff delays increase exponentially.

        Delays should follow the pattern: base * (2 ** attempt)
        capped at max_delay.
        """
        # Arrange
        delays = []
        original_sleep = asyncio.sleep

        async def mock_sleep(seconds):
            delays.append(seconds)
            # Don't actually sleep in test

        call_count = 0

        @with_backoff(
            max_retries=4,
            base_delay=1.0,
            max_delay=10.0,
            exponential_base=2.0,
            exceptions=(ValueError,),
        )
        async def always_fails():
            nonlocal call_count
            call_count += 1
            raise ValueError("Always fails")

        # Act: Patch asyncio.sleep and run
        with patch("asyncio.sleep", side_effect=mock_sleep):
            with pytest.raises(ValueError):
                await always_fails()

        # Assert: Verify exponential delays
        # Expected delays: 1.0, 2.0, 4.0, 8.0 (then fail)
        expected_delays = [1.0, 2.0, 4.0, 8.0]
        assert len(delays) == 4, f"Expected 4 delays, got {len(delays)}"

        for actual, expected in zip(delays, expected_delays):
            assert abs(actual - expected) < 0.01, (
                f"Expected delay {expected}, got {actual}"
            )

    @pytest.mark.asyncio
    async def test_circuit_breaker_with_sync_function(self):
        """
        Test that circuit breaker works with synchronous functions.

        The breaker should detect and handle both async and sync functions.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="sync_test",
            failure_threshold=2,
        )

        call_count = 0

        def sync_func():
            nonlocal call_count
            call_count += 1
            return f"sync_result_{call_count}"

        # Act
        result = await breaker.call(sync_func)

        # Assert
        assert result == "sync_result_1"
        assert breaker.state == CircuitState.CLOSED
        assert breaker.successes == 1

    @pytest.mark.asyncio
    async def test_manual_reset(self):
        """
        Test that manual reset() returns circuit to CLOSED state.

        Reset should clear all counters and allow calls again.
        """
        # Arrange
        breaker = CircuitBreaker(
            name="reset_test",
            failure_threshold=2,
            recovery_timeout=3600,  # Long timeout
        )

        async def failing():
            raise Exception("Fail")

        # Trip the breaker
        for _ in range(2):
            with pytest.raises(Exception):
                await breaker.call(failing)

        assert breaker.state == CircuitState.OPEN

        # Act: Manual reset
        breaker.reset()

        # Assert
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failures == 0
        assert breaker.successes == 0
        assert breaker.half_open_calls == 0
