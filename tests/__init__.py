"""
Test Suite for Reply Guy Bot.

Structure:
    tests/
    ├── conftest.py          # Shared fixtures and configuration
    ├── unit/                # Mock-based unit tests
    │   ├── test_scheduler.py
    │   ├── test_rate_limiter.py
    │   └── test_circuit_breaker.py
    ├── integration/         # Integration tests (mocked external APIs)
    │   ├── test_integration.py
    │   └── test_x_delegate_rate_limiting.py
    └── real/                # Real functionality tests
        ├── test_scheduler_real.py
        ├── test_rate_limiter_real.py
        ├── test_circuit_breaker_real.py
        ├── test_database_real.py
        ├── test_ai_client_real.py
        ├── test_background_worker_real.py
        └── test_bot_orchestration_real.py

Run tests:
    pytest tests/                    # All tests
    pytest tests/unit/               # Unit tests only
    pytest tests/real/               # Real functionality tests only
    pytest tests/integration/        # Integration tests only
    pytest tests/ -v                 # Verbose output
    pytest tests/ --cov=src          # With coverage
    pytest tests/ -m real            # Tests marked @pytest.mark.real
"""
