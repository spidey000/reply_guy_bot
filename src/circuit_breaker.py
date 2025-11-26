"""
Circuit Breaker Pattern - Resilient external service calls.

This module implements the circuit breaker pattern to prevent cascading failures
when external services are unavailable or degraded.

Pattern States:
    CLOSED: Normal operation, calls pass through
    OPEN: Service is failing, calls blocked immediately
    HALF_OPEN: Testing recovery, limited calls allowed

Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │  CLOSED → (failures >= threshold) → OPEN                    │
    │  OPEN → (timeout elapsed) → HALF_OPEN                       │
    │  HALF_OPEN → (success) → CLOSED                            │
    │  HALF_OPEN → (failure) → OPEN                              │
    └─────────────────────────────────────────────────────────────┘

Usage:
    breaker = CircuitBreaker("twitter_api", failure_threshold=5)

    result = await breaker.call(twitter_client.post, tweet_data)
"""

import asyncio
import logging
import time
from enum import Enum
from functools import wraps
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"         # Normal operation
    OPEN = "open"             # Blocking calls
    HALF_OPEN = "half_open"   # Testing recovery


class CircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker for external service calls.

    Prevents cascading failures by failing fast when a service
    is experiencing issues.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        half_open_max_calls: int = 3,
        expected_exceptions: tuple = (Exception,),
    ):
        """
        Initialize circuit breaker.

        Args:
            name: Identifier for this circuit breaker.
            failure_threshold: Number of failures before opening circuit.
            recovery_timeout: Seconds to wait before testing recovery.
            half_open_max_calls: Max calls allowed in half-open state.
            expected_exceptions: Exceptions that should trip the circuit.
        """
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.expected_exceptions = expected_exceptions

        self.state = CircuitState.CLOSED
        self.failures = 0
        self.successes = 0
        self.last_failure_time: Optional[float] = None
        self.half_open_calls = 0

        logger.info(
            f"Circuit breaker '{name}' initialized: "
            f"threshold={failure_threshold}, timeout={recovery_timeout}s"
        )

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to execute (can be sync or async).
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.

        Returns:
            Result from the function call.

        Raises:
            CircuitBreakerError: If circuit is open.
            Exception: Original exception from the function.
        """
        # Check if we should attempt the call
        if not self._can_attempt():
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' is OPEN - "
                f"wait {self._get_wait_time():.0f}s before retry"
            )

        try:
            # Execute function (handle both sync and async)
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Record success
            self.record_success()
            return result

        except self.expected_exceptions as e:
            # Record failure
            self.record_failure()

            logger.warning(
                f"Circuit breaker '{self.name}': call failed "
                f"({self.failures}/{self.failure_threshold}) - {e}"
            )
            raise

    def _can_attempt(self) -> bool:
        """Check if we can attempt a call based on current state."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if recovery timeout has elapsed
            if self._should_attempt_reset():
                self._transition_to_half_open()
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # Allow limited calls in half-open state
            if self.half_open_calls < self.half_open_max_calls:
                self.half_open_calls += 1
                return True
            return False

        return False

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if self.last_failure_time is None:
            return True

        elapsed = time.time() - self.last_failure_time
        return elapsed >= self.recovery_timeout

    def _get_wait_time(self) -> float:
        """Get remaining wait time before circuit can close."""
        if self.last_failure_time is None:
            return 0.0

        elapsed = time.time() - self.last_failure_time
        remaining = self.recovery_timeout - elapsed
        return max(0.0, remaining)

    def record_success(self) -> None:
        """Record successful call."""
        self.successes += 1

        if self.state == CircuitState.HALF_OPEN:
            # Successful call in half-open state - close circuit
            logger.info(
                f"Circuit breaker '{self.name}': "
                f"recovery successful, closing circuit"
            )
            self.state = CircuitState.CLOSED
            self.failures = 0
            self.half_open_calls = 0

        elif self.state == CircuitState.CLOSED:
            # Reset failure counter on success
            self.failures = 0

    def record_failure(self) -> None:
        """Record failed call, potentially open circuit."""
        self.failures += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Failed during recovery - reopen circuit
            logger.warning(
                f"Circuit breaker '{self.name}': "
                f"recovery failed, reopening circuit"
            )
            self._transition_to_open()
            self.half_open_calls = 0

        elif self.state == CircuitState.CLOSED:
            # Check if we should open
            if self.failures >= self.failure_threshold:
                logger.error(
                    f"Circuit breaker '{self.name}': "
                    f"failure threshold reached ({self.failures}), opening circuit"
                )
                self._transition_to_open()

    def _transition_to_open(self) -> None:
        """Transition to OPEN state."""
        self.state = CircuitState.OPEN
        logger.warning(
            f"Circuit breaker '{self.name}' → OPEN "
            f"(recovery in {self.recovery_timeout}s)"
        )

    def _transition_to_half_open(self) -> None:
        """Transition to HALF_OPEN state."""
        self.state = CircuitState.HALF_OPEN
        self.half_open_calls = 0
        logger.info(
            f"Circuit breaker '{self.name}' → HALF_OPEN "
            f"(testing recovery with max {self.half_open_max_calls} calls)"
        )

    def reset(self) -> None:
        """Manually reset circuit breaker to CLOSED state."""
        logger.info(f"Circuit breaker '{self.name}': manual reset")
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.successes = 0
        self.half_open_calls = 0
        self.last_failure_time = None

    def get_status(self) -> dict:
        """
        Get current circuit breaker status.

        Returns:
            Dictionary with status information.
        """
        return {
            "name": self.name,
            "state": self.state.value,
            "failures": self.failures,
            "successes": self.successes,
            "failure_threshold": self.failure_threshold,
            "wait_time_seconds": self._get_wait_time(),
            "half_open_calls": self.half_open_calls if self.state == CircuitState.HALF_OPEN else 0,
        }


def with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    Decorator for exponential backoff retry.

    Retries the decorated function with exponentially increasing delays.

    Delay formula: min(base_delay * (exponential_base ** attempt), max_delay)

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds.
        max_delay: Maximum delay in seconds.
        exponential_base: Base for exponential calculation.
        exceptions: Tuple of exceptions to catch and retry.

    Usage:
        @with_backoff(max_retries=3, base_delay=1, max_delay=10)
        async def fetch_data():
            # Your code here
            pass

    Example delays (base=1, exponential_base=2):
        Attempt 1: 1s
        Attempt 2: 2s
        Attempt 3: 4s
        Attempt 4: 8s
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    # Execute function
                    if asyncio.iscoroutinefunction(func):
                        return await func(*args, **kwargs)
                    else:
                        return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    # Don't retry on last attempt
                    if attempt == max_retries:
                        logger.error(
                            f"Function '{func.__name__}' failed after "
                            f"{max_retries} retries: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(
                        base_delay * (exponential_base ** attempt),
                        max_delay
                    )

                    logger.warning(
                        f"Function '{func.__name__}' failed (attempt {attempt + 1}/"
                        f"{max_retries + 1}), retrying in {delay:.1f}s: {e}"
                    )

                    await asyncio.sleep(delay)

            # Should never reach here, but just in case
            if last_exception:
                raise last_exception

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> Any:
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        logger.error(
                            f"Function '{func.__name__}' failed after "
                            f"{max_retries} retries: {e}"
                        )
                        raise

                    delay = min(
                        base_delay * (exponential_base ** attempt),
                        max_delay
                    )

                    logger.warning(
                        f"Function '{func.__name__}' failed (attempt {attempt + 1}/"
                        f"{max_retries + 1}), retrying in {delay:.1f}s: {e}"
                    )

                    time.sleep(delay)

            if last_exception:
                raise last_exception

        # Return appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
