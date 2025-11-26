# Real Functionality Tests - Execution Checklist

## Pre-Implementation Checklist

### Environment Setup
- [ ] Python 3.11+ installed
- [ ] Virtual environment activated
- [ ] All dependencies from `requirements.txt` installed
- [ ] Development dependencies installed:
  ```bash
  pip install pytest>=8.0.0 pytest-asyncio>=0.23.0 pytest-cov>=4.0.0 \
              pytest-timeout>=2.2.0 freezegun>=1.4.0 respx>=0.20.0
  ```

### Repository Verification
- [ ] Git status is clean (no uncommitted changes)
- [ ] On correct branch (main or feature/real-tests)
- [ ] Pull latest changes from remote
- [ ] Existing tests pass: `pytest tests/unit/ -v`

### Documentation Review
- [ ] Read `IMPLEMENTATION_SUMMARY.md`
- [ ] Review `REAL_TESTS_WORKFLOW.md`
- [ ] Understand `AGENT_EXECUTION_PLAN.json`
- [ ] Skim `TESTING_QUICK_REFERENCE.md`

### Directory Structure
- [ ] Create `tests/real/` directory
  ```bash
  mkdir -p tests/real
  ```
- [ ] Create `tests/real/__init__.py`
  ```bash
  touch tests/real/__init__.py
  ```
- [ ] Verify `tests/conftest.py` has new fixtures

---

## Phase 1: Core Logic Tests (Days 1-2)

### Task 1.1: Scheduler Real Output Tests
- [ ] Create `tests/real/test_scheduler_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from datetime import datetime, timedelta
  from src.scheduler import calculate_schedule_time, get_delay_description
  ```
- [ ] Implement test cases:
  - [ ] `test_calculate_schedule_time_returns_future_time()`
  - [ ] `test_quiet_hours_respected()`
  - [ ] `test_jitter_applied()`
  - [ ] `test_quiet_hours_spanning_midnight()`
  - [ ] `test_delay_description_accuracy()`
- [ ] Run tests: `pytest tests/real/test_scheduler_real.py -v`
- [ ] Verify all 5 tests pass
- [ ] Check coverage: `pytest tests/real/test_scheduler_real.py --cov=src.scheduler`

### Task 1.2: Rate Limiter Real Behavior Tests
- [ ] Create `tests/real/test_rate_limiter_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from datetime import datetime, timedelta
  from src.rate_limiter import RateLimiter
  ```
- [ ] Implement test cases:
  - [ ] `test_rate_limit_enforcement()`
  - [ ] `test_rate_limit_recovery()`
  - [ ] `test_get_status_accuracy()`
  - [ ] `test_sliding_window_behavior()`
  - [ ] `test_warning_threshold_triggered()`
  - [ ] `test_wait_time_calculation()`
- [ ] Run tests: `pytest tests/real/test_rate_limiter_real.py -v`
- [ ] Verify all 6 tests pass
- [ ] Check coverage: `pytest tests/real/test_rate_limiter_real.py --cov=src.rate_limiter`

### Task 1.3: Circuit Breaker State Tests
- [ ] Create `tests/real/test_circuit_breaker_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from src.circuit_breaker import CircuitBreaker, CircuitState, with_backoff
  ```
- [ ] Implement test cases:
  - [ ] `test_closed_to_open_transition()`
  - [ ] `test_open_to_half_open_transition()`
  - [ ] `test_half_open_success_closes_circuit()`
  - [ ] `test_half_open_failure_reopens_circuit()`
  - [ ] `test_get_status_accuracy()`
  - [ ] `test_half_open_call_limiting()`
  - [ ] `test_backoff_decorator_retry_logic()`
  - [ ] `test_backoff_exponential_delays()`
- [ ] Run tests: `pytest tests/real/test_circuit_breaker_real.py -v`
- [ ] Verify all 8 tests pass
- [ ] Check coverage: `pytest tests/real/test_circuit_breaker_real.py --cov=src.circuit_breaker`

### Phase 1 Completion
- [ ] Run all Phase 1 tests: `pytest tests/real/ -v`
- [ ] Verify 19 tests pass
- [ ] Check total execution time (should be < 5 seconds)
- [ ] Generate coverage report: `pytest tests/real/ --cov=src --cov-report=html`
- [ ] Review coverage (should be 90%+ for tested components)
- [ ] Commit changes:
  ```bash
  git add tests/real/test_scheduler_real.py tests/real/test_rate_limiter_real.py tests/real/test_circuit_breaker_real.py
  git commit -m "feat: Add Phase 1 real functionality tests (core logic)"
  ```

---

## Phase 2: State Management Tests (Days 3-4)

### Task 2.1: Database State Flow Tests
- [ ] Create `tests/real/test_database_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from datetime import datetime, timedelta
  from src.database import Database
  ```
- [ ] Verify `in_memory_db` fixture works
- [ ] Implement test cases:
  - [ ] `test_add_to_queue_creates_pending()`
  - [ ] `test_approve_tweet_state_transition()`
  - [ ] `test_mark_as_posted_state_transition()`
  - [ ] `test_mark_as_failed_stores_error()`
  - [ ] `test_get_pending_tweets_filters_correctly()`
  - [ ] `test_dead_letter_queue_workflow()`
  - [ ] `test_recover_stale_tweets()`
  - [ ] `test_get_pending_count()`
  - [ ] `test_health_check_connection()`
- [ ] Run tests: `pytest tests/real/test_database_real.py -v`
- [ ] Verify all 9 tests pass
- [ ] Check coverage: `pytest tests/real/test_database_real.py --cov=src.database`

### Phase 2 Completion
- [ ] Run all tests so far: `pytest tests/real/ -v`
- [ ] Verify 28 tests pass (19 + 9)
- [ ] Check total execution time (should be < 8 seconds)
- [ ] Generate coverage report
- [ ] Commit changes:
  ```bash
  git add tests/real/test_database_real.py
  git commit -m "feat: Add Phase 2 real functionality tests (state management)"
  ```

---

## Phase 3: Integration Flow Tests (Days 5-7)

### Task 3.1: AI Client Retry Tests
- [ ] Create `tests/real/test_ai_client_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from unittest.mock import patch, AsyncMock
  from src.ai_client import AIClient
  from openai import RateLimitError, APIConnectionError
  ```
- [ ] Implement test cases:
  - [ ] `test_retry_on_transient_error()`
  - [ ] `test_retry_exhaustion()`
  - [ ] `test_exponential_backoff_delays()`
  - [ ] `test_successful_generation()`
  - [ ] `test_reply_cleaning()`
  - [ ] `test_health_check()`
- [ ] Run tests: `pytest tests/real/test_ai_client_real.py -v`
- [ ] Verify all 6 tests pass
- [ ] Check coverage: `pytest tests/real/test_ai_client_real.py --cov=src.ai_client`

### Task 3.2: Background Worker Flow Tests
- [ ] Create `tests/real/test_background_worker_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from datetime import datetime
  from src.background_worker import process_pending_tweets, get_queue_status
  ```
- [ ] Implement test cases:
  - [ ] `test_process_pending_tweets_finds_ready_tweets()`
  - [ ] `test_successful_publication_flow()`
  - [ ] `test_failed_publication_adds_to_dlq()`
  - [ ] `test_exception_handling_in_worker()`
  - [ ] `test_get_queue_status()`
- [ ] Run tests: `pytest tests/real/test_background_worker_real.py -v`
- [ ] Verify all 5 tests pass
- [ ] Check coverage: `pytest tests/real/test_background_worker_real.py --cov=src.background_worker`

### Task 3.3: Bot Orchestration Tests
- [ ] Create `tests/real/test_bot_orchestration_real.py`
- [ ] Import required modules:
  ```python
  import pytest
  from src.bot import ReplyGuyBot
  ```
- [ ] Implement test cases:
  - [ ] `test_initialization_sequence()`
  - [ ] `test_health_check_all_components()`
  - [ ] `test_approval_workflow()`
  - [ ] `test_rejection_workflow()`
  - [ ] `test_circuit_breaker_integration()`
  - [ ] `test_crash_recovery_on_startup()`
- [ ] Run tests: `pytest tests/real/test_bot_orchestration_real.py -v`
- [ ] Verify all 6 tests pass
- [ ] Check coverage: `pytest tests/real/test_bot_orchestration_real.py --cov=src.bot`

### Phase 3 Completion
- [ ] Run all tests: `pytest tests/real/ -v`
- [ ] Verify 45 tests pass (28 + 17)
- [ ] Check total execution time (should be < 30 seconds)
- [ ] Generate final coverage report
- [ ] Commit changes:
  ```bash
  git add tests/real/test_ai_client_real.py tests/real/test_background_worker_real.py tests/real/test_bot_orchestration_real.py
  git commit -m "feat: Add Phase 3 real functionality tests (integration)"
  ```

---

## Final Verification

### Test Suite Health
- [ ] Run full test suite: `pytest tests/ -v`
- [ ] Verify all 45 real tests pass
- [ ] Verify existing mock tests still pass
- [ ] Check for flaky tests (run suite 3 times):
  ```bash
  pytest tests/real/ -v && pytest tests/real/ -v && pytest tests/real/ -v
  ```

### Coverage Analysis
- [ ] Generate full coverage report:
  ```bash
  pytest tests/real/ --cov=src --cov-report=html --cov-report=term
  ```
- [ ] Open `htmlcov/index.html` in browser
- [ ] Verify coverage meets targets:
  - [ ] Scheduler: > 90%
  - [ ] Rate Limiter: > 90%
  - [ ] Circuit Breaker: > 90%
  - [ ] Database: > 85% (some methods hard to test)
  - [ ] AI Client: > 80%
  - [ ] Background Worker: > 85%
  - [ ] Bot: > 75% (high complexity)

### Performance Testing
- [ ] Run with timing: `pytest tests/real/ -v --durations=10`
- [ ] Identify slowest tests (should all be < 5s)
- [ ] Verify total execution < 30s
- [ ] Run in parallel: `pytest tests/real/ -n auto`

### Quality Checks
- [ ] All tests have docstrings
- [ ] All tests use `@pytest.mark.real`
- [ ] Integration tests use `@pytest.mark.integration`
- [ ] No external API calls made
- [ ] No flaky tests detected
- [ ] All fixtures properly cleanup

---

## Documentation Updates

### Update README.md
- [ ] Add section about real tests:
  ```markdown
  ## Testing

  This project has two types of tests:
  - **Unit Tests** (`tests/unit/`): Mock-based tests for isolation
  - **Real Tests** (`tests/real/`): Real functionality tests

  Run tests with:
  ```bash
  # All tests
  pytest tests/ -v

  # Just real functionality tests
  pytest tests/real/ -v

  # With coverage
  pytest tests/real/ --cov=src
  ```
  ```

### Update .gitignore
- [ ] Ensure test artifacts are ignored:
  ```
  .pytest_cache/
  .coverage
  htmlcov/
  test_cookies.json
  test_audit.log
  ```

### Create Test Report
- [ ] Document findings in `TEST_REPORT.md`:
  - [ ] Total tests created
  - [ ] Coverage achieved
  - [ ] Execution time
  - [ ] Patterns discovered
  - [ ] Issues encountered
  - [ ] Recommendations

---

## CI/CD Integration

### GitHub Actions (if applicable)
- [ ] Add workflow file `.github/workflows/real-tests.yml`:
  ```yaml
  name: Real Functionality Tests

  on: [push, pull_request]

  jobs:
    test:
      runs-on: ubuntu-latest
      steps:
        - uses: actions/checkout@v3
        - uses: actions/setup-python@v4
          with:
            python-version: '3.11'
        - run: pip install -r requirements-dev.txt
        - run: pytest tests/real/ -v --cov=src
  ```

---

## Final Commit and Push

- [ ] Review all changes: `git status`
- [ ] Create final commit if needed
- [ ] Push to remote:
  ```bash
  git push origin <branch-name>
  ```
- [ ] Create pull request if using feature branch
- [ ] Document changes in PR description

---

## Success Criteria Verification

### Quantitative Metrics
- [ ] 45 tests created ✓
- [ ] 100% pass rate ✓
- [ ] 90%+ coverage for core components ✓
- [ ] < 30s total execution time ✓
- [ ] 0 flaky tests ✓

### Qualitative Metrics
- [ ] Tests verify real behavior, not mocks
- [ ] Tests are deterministic and repeatable
- [ ] Tests are well-documented
- [ ] Tests are maintainable
- [ ] Tests provide value beyond mocks

---

## Post-Implementation Review

### Retrospective Questions
- [ ] What worked well?
- [ ] What was challenging?
- [ ] What would you do differently?
- [ ] What patterns emerged?
- [ ] What should be documented for future tests?

### Knowledge Sharing
- [ ] Update team on new testing approach
- [ ] Share patterns and learnings
- [ ] Document edge cases discovered
- [ ] Create examples for future reference

---

## Maintenance Plan

### Weekly
- [ ] Run full test suite
- [ ] Check for new failures
- [ ] Review coverage trends

### Monthly
- [ ] Review slow tests
- [ ] Refactor duplicated test code
- [ ] Update fixtures as needed

### Quarterly
- [ ] Comprehensive test suite review
- [ ] Update testing documentation
- [ ] Evaluate new testing tools/patterns

---

**Created**: 2025-11-26
**Status**: Ready for Execution
**Estimated Duration**: 7 days
**Total Tests**: 45
**Total Files**: 7 test files + fixtures
