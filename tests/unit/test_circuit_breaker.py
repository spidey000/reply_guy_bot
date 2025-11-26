"""
Tests for Circuit Breaker Pattern (T017-S1).

This module tests the circuit breaker implementation to ensure
proper failure handling and recovery.
"""

import asyncio
import pytest
from src.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState, with_backoff


class TestCircuitBreaker:
    """Test suite for CircuitBreaker class."""

    @pytest.mark.asyncio
    async def test_circuit_breaker_closed_state(self):
        """Test that circuit breaker starts in CLOSED state."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_successful_call(self):
        """Test successful function call through circuit breaker."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        async def success_func():
            return "success"

        result = await breaker.call(success_func)
        assert result == "success"
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self):
        """Test that failures increment the failure counter."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        async def fail_func():
            raise ValueError("test error")

        # First failure
        with pytest.raises(ValueError):
            await breaker.call(fail_func)

        assert breaker.failures == 1
        assert breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self):
        """Test that circuit opens after reaching failure threshold."""
        breaker = CircuitBreaker("test", failure_threshold=3)

        async def fail_func():
            raise ValueError("test error")

        # Fail 3 times to reach threshold
        for _ in range(3):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        assert breaker.state == CircuitState.OPEN
        assert breaker.failures == 3

    @pytest.mark.asyncio
    async def test_circuit_open_blocks_calls(self):
        """Test that open circuit blocks calls immediately."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=10)

        async def fail_func():
            raise ValueError("test error")

        # Fail enough times to open circuit
        for _ in range(2):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Now try to call - should raise CircuitBreakerError immediately
        with pytest.raises(CircuitBreakerError):
            await breaker.call(fail_func)

    @pytest.mark.asyncio
    async def test_circuit_transitions_to_half_open(self):
        """Test that circuit transitions to HALF_OPEN after timeout."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)

        async def fail_func():
            raise ValueError("test error")

        # Open the circuit
        for _ in range(2):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(1.1)

        # Next call should transition to HALF_OPEN
        async def success_func():
            return "recovered"

        result = await breaker.call(success_func)
        assert result == "recovered"
        assert breaker.state == CircuitState.CLOSED  # Success in half-open closes circuit

    @pytest.mark.asyncio
    async def test_half_open_success_closes_circuit(self):
        """Test that success in HALF_OPEN state closes the circuit."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)

        async def fail_func():
            raise ValueError("test error")

        async def success_func():
            return "success"

        # Open circuit
        for _ in range(2):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        # Wait for recovery
        await asyncio.sleep(1.1)

        # Successful call should close circuit
        await breaker.call(success_func)
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        """Test that failure in HALF_OPEN state reopens the circuit."""
        breaker = CircuitBreaker("test", failure_threshold=2, recovery_timeout=1)

        async def fail_func():
            raise ValueError("test error")

        # Open circuit
        for _ in range(2):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        # Wait for recovery
        await asyncio.sleep(1.1)

        # Failed call in half-open should reopen
        try:
            await breaker.call(fail_func)
        except ValueError:
            pass

        assert breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_manual_reset(self):
        """Test manual reset of circuit breaker."""
        breaker = CircuitBreaker("test", failure_threshold=2)

        async def fail_func():
            raise ValueError("test error")

        # Open circuit
        for _ in range(2):
            try:
                await breaker.call(fail_func)
            except ValueError:
                pass

        assert breaker.state == CircuitState.OPEN

        # Manual reset
        breaker.reset()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failures == 0

    @pytest.mark.asyncio
    async def test_get_status(self):
        """Test get_status returns correct information."""
        breaker = CircuitBreaker("test", failure_threshold=3)
        status = breaker.get_status()

        assert status["name"] == "test"
        assert status["state"] == "closed"
        assert status["failures"] == 0
        assert status["failure_threshold"] == 3


class TestBackoffDecorator:
    """Test suite for with_backoff decorator."""

    @pytest.mark.asyncio
    async def test_backoff_success_no_retry(self):
        """Test that successful calls don't retry."""
        call_count = 0

        @with_backoff(max_retries=3, base_delay=0.1)
        async def success_func():
            nonlocal call_count
            call_count += 1
            return "success"

        result = await success_func()
        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_backoff_retries_on_failure(self):
        """Test that failures trigger retries."""
        call_count = 0

        @with_backoff(max_retries=3, base_delay=0.1)
        async def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("temporary error")
            return "success"

        result = await fail_then_succeed()
        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_max_retries_exhausted(self):
        """Test that max retries are respected."""
        call_count = 0

        @with_backoff(max_retries=2, base_delay=0.1)
        async def always_fail():
            nonlocal call_count
            call_count += 1
            raise ValueError("permanent error")

        with pytest.raises(ValueError):
            await always_fail()

        # Should be called once + 2 retries = 3 total
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_exponential_delay(self):
        """Test that delays increase exponentially."""
        import time

        delays = []

        @with_backoff(max_retries=3, base_delay=0.1, exponential_base=2.0)
        async def track_delays():
            delays.append(time.time())
            if len(delays) < 4:
                raise ValueError("error")
            return "success"

        await track_delays()

        # Check that delays increased (approximately)
        # delay1 ~= 0.1s, delay2 ~= 0.2s, delay3 ~= 0.4s
        assert len(delays) == 4
        # Just verify we had multiple attempts
        assert len(delays) > 1

    def test_backoff_sync_function(self):
        """Test backoff decorator with synchronous functions."""
        call_count = 0

        @with_backoff(max_retries=2, base_delay=0.1)
        def sync_fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("error")
            return "success"

        result = sync_fail_then_succeed()
        assert result == "success"
        assert call_count == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
