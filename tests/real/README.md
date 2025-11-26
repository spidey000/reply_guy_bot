# Real Functionality Tests

This directory contains tests that verify REAL component behavior and expected outputs, as opposed to the mock-based tests in `tests/unit/`.

## Philosophy

These tests focus on:
- **Real outputs** from pure functions
- **Real state transitions** in state machines
- **Real data flows** through components
- **Real retry/backoff behavior**
- **Real integration** between components

## Test Files

| File | Component | Tests | Focus |
|------|-----------|-------|-------|
| `test_scheduler_real.py` | Scheduler | 5 | Time calculations, quiet hours, jitter |
| `test_rate_limiter_real.py` | Rate Limiter | 6 | Sliding window, limits, wait times |
| `test_circuit_breaker_real.py` | Circuit Breaker | 8 | State transitions, backoff logic |
| `test_database_real.py` | Database | 9 | CRUD operations, state flow, DLQ |
| `test_ai_client_real.py` | AI Client | 6 | Retry logic, backoff delays |
| `test_background_worker_real.py` | Worker | 5 | Tweet processing, error handling |
| `test_bot_orchestration_real.py` | Bot | 6 | Component wiring, workflows |

## Running Tests

```bash
# Run all real tests
pytest tests/real/ -v

# Run specific file
pytest tests/real/test_scheduler_real.py -v

# Run with coverage
pytest tests/real/ --cov=src --cov-report=html

# Run marked tests only
pytest tests/real/ -m real -v

# Run in parallel (faster)
pytest tests/real/ -n auto
```

## Markers

Tests in this directory use the following markers:

- `@pytest.mark.real` - Real functionality test (not mock-based)
- `@pytest.mark.integration` - Integration test (multiple components)
- `@pytest.mark.slow` - Slow test (> 1 second)

## Common Patterns

### 1. Pure Function Output Testing

```python
@pytest.mark.real
def test_scheduler_returns_future_time():
    """Verify scheduled time is in future within expected range."""
    now = datetime.now()
    scheduled = calculate_schedule_time(base_time=now)

    # Real behavior verification
    assert scheduled > now
    delta_minutes = (scheduled - now).total_seconds() / 60
    assert 15 <= delta_minutes <= 125
```

### 2. State Transition Testing

```python
@pytest.mark.real
async def test_circuit_breaker_opens_after_threshold():
    """Verify circuit opens after failure threshold."""
    breaker = CircuitBreaker("test", failure_threshold=3)

    # Trigger real failures
    for _ in range(3):
        try:
            await breaker.call(failing_function)
        except:
            pass

    # Verify real state change
    assert breaker.state == CircuitState.OPEN
    assert breaker.failures == 3
```

### 3. Time-Based Testing

```python
@pytest.mark.real
async def test_rate_limit_recovery(time_controller):
    """Verify rate limiter allows posts after time passes."""
    limiter = RateLimiter(max_per_hour=2)

    # Use up the limit
    await limiter.record_post()
    await limiter.record_post()
    assert not await limiter.can_post()

    # Advance time
    time_controller.freeze(datetime.now())
    time_controller.advance(minutes=61)

    # Should be able to post again
    assert await limiter.can_post()
```

### 4. Database State Flow Testing

```python
@pytest.mark.real
@pytest.mark.integration
async def test_tweet_complete_lifecycle(in_memory_db):
    """Test complete tweet lifecycle: pending → approved → posted."""
    db = Database(client=in_memory_db)

    # Create pending
    tweet_id = await db.add_to_queue(...)
    tweet = await db.get_tweet(tweet_id)
    assert tweet['status'] == 'pending'

    # Approve
    await db.approve_tweet(tweet_id, scheduled_at)
    tweet = await db.get_tweet(tweet_id)
    assert tweet['status'] == 'approved'

    # Mark posted
    await db.mark_as_posted(tweet_id)
    tweet = await db.get_tweet(tweet_id)
    assert tweet['status'] == 'posted'
```

## Available Fixtures

From `tests/conftest.py`:

### Time Fixtures
- `frozen_time` - Freeze time at specific datetime
- `time_controller` - Control time (freeze, advance)

### Database Fixtures
- `in_memory_db` - SQLite in-memory database with schema
- `in_memory_db_schema` - SQL schema for test database
- `sample_multiple_tweets` - Multiple tweets in different states

### Mock Component Fixtures
- `mock_database` - Mock database with common methods
- `mock_ghost_delegate` - Mock Ghost Delegate
- `mock_ai_client` - Mock AI client
- `mock_telegram` - Mock Telegram client

### Test Function Fixtures
- `failing_function` - Function that always fails
- `succeeding_function` - Function that always succeeds
- `intermittent_function` - Function that fails N times then succeeds

### Assertion Helpers
- `assert_datetime_close` - Assert two datetimes are within tolerance
- `assert_status_transition` - Assert valid status transition

## What to Mock vs What to Run Real

| Component | Mock | Run Real |
|-----------|------|----------|
| Scheduler | Config (optional) | All calculations |
| Rate Limiter | Time | All logic |
| Circuit Breaker | Time, test functions | State machine |
| Database | Supabase client | All operations |
| AI Client | OpenAI API | Retry logic |
| Worker | DB, Ghost, Telegram | Worker logic |
| Bot | All external APIs | Bot logic |

## Quality Standards

Each test should:
- Have a clear docstring explaining what it tests
- Have at least 2 assertions
- Complete in under 5 seconds
- Be independent (no test order dependency)
- Clean up after itself

## Debugging Failed Tests

```bash
# Run with verbose output
pytest tests/real/test_scheduler_real.py -vv

# Run with print statements visible
pytest tests/real/test_scheduler_real.py -s

# Run with debugger on failure
pytest tests/real/test_scheduler_real.py --pdb

# Run only failed tests from last run
pytest tests/real/ --lf

# Show full diff on assertion failures
pytest tests/real/ -vv --tb=long
```

## Contributing

When adding new tests:

1. Place in appropriate file based on component
2. Use `@pytest.mark.real` decorator
3. Follow naming convention: `test_<component>_<behavior>_<result>`
4. Add docstring explaining what's being tested
5. Use real fixtures from conftest.py
6. Ensure test is deterministic

## Examples

See `TESTING_QUICK_REFERENCE.md` in project root for detailed examples and patterns.

## Documentation

- `REAL_TESTS_WORKFLOW.md` - Detailed workflow and specifications
- `AGENT_EXECUTION_PLAN.json` - Execution plan for agent orchestration
- `TESTING_QUICK_REFERENCE.md` - Quick reference guide
- `IMPLEMENTATION_SUMMARY.md` - Implementation overview

---

Created: 2025-11-26
Total Tests: 45 across 7 files
