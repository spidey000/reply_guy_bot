"""
Test script for Ghost Delegate security hardening features.

This script tests the new security features without making actual Twitter API calls.
"""

import asyncio
import logging
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Set dummy environment variables for testing
os.environ.update({
    "DUMMY_USERNAME": "test_dummy",
    "DUMMY_EMAIL": "dummy@test.com",
    "DUMMY_PASSWORD": "test_password",
    "MAIN_ACCOUNT_HANDLE": "test_main",
    "AI_API_KEY": "test_key",
    "AI_BASE_URL": "http://localhost",
    "AI_MODEL": "test-model",
    "TELEGRAM_BOT_TOKEN": "test_token",
    "TELEGRAM_CHAT_ID": "test_chat",
    "SUPABASE_URL": "http://localhost",
    "SUPABASE_KEY": "test_key",
    "GHOST_DELEGATE_ENABLED": "true",
    "GHOST_DELEGATE_SWITCH_TIMEOUT": "30",
    "MAX_POSTS_PER_HOUR": "15",
    "MAX_POSTS_PER_DAY": "50",
    "RATE_LIMIT_WARNING_THRESHOLD": "0.8",
})

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def test_session_validation():
    """Test session validation logic."""
    logger.info("\n=== Testing Session Validation ===")

    from src.x_delegate import GhostDelegate

    delegate = GhostDelegate()

    # Test 1: Not authenticated
    result = await delegate.validate_session()
    assert result is False, "Should fail when not authenticated"
    logger.info("✓ Session validation fails when not authenticated")

    # Test 2: Kill switch active
    delegate._is_authenticated = True
    delegate.client = MagicMock()
    delegate._kill_switch = True
    result = await delegate.validate_session()
    assert result is False, "Should fail when kill switch is active"
    logger.info("✓ Session validation fails when kill switch is active")

    logger.info("Session validation tests passed!")


async def test_emergency_stop():
    """Test emergency stop functionality."""
    logger.info("\n=== Testing Emergency Stop ===")

    from src.x_delegate import GhostDelegate, COOKIE_FILE

    delegate = GhostDelegate()
    delegate._is_authenticated = True
    delegate.client = MagicMock()
    delegate._current_account = "main"

    # Create a dummy cookie file
    COOKIE_FILE.touch()

    # Execute emergency stop
    await delegate.emergency_stop()

    # Verify state
    assert delegate._kill_switch is True, "Kill switch should be active"
    assert delegate._is_authenticated is False, "Should be marked as unauthenticated"
    assert delegate._current_account == "none", "Current account should be none"
    assert not COOKIE_FILE.exists(), "Cookie file should be deleted"

    logger.info("✓ Kill switch activated")
    logger.info("✓ Authentication cleared")
    logger.info("✓ Cookies deleted")
    logger.info("Emergency stop tests passed!")


async def test_context_manager():
    """Test the as_main context manager."""
    logger.info("\n=== Testing Context Manager ===")

    from src.x_delegate import GhostDelegate

    # Mock the Twikit Client
    with patch("src.x_delegate.Client") as MockClient:
        delegate = GhostDelegate()
        delegate.client = MagicMock()
        delegate._is_authenticated = True
        delegate.dummy_user = MagicMock(id="dummy_123")
        delegate.main_user = MagicMock(id="main_456")

        # Mock validate_session to return True
        delegate.validate_session = AsyncMock(return_value=True)

        # Test successful context manager usage
        async with delegate.as_main():
            assert delegate._current_account == "main", "Should be switched to main"
            logger.info("✓ Successfully switched to main account")

        # After context exits, should revert
        assert delegate._current_account == "dummy", "Should revert to dummy"
        logger.info("✓ Successfully reverted to dummy account")

        logger.info("Context manager tests passed!")


async def test_audit_logging():
    """Test audit logging functionality."""
    logger.info("\n=== Testing Audit Logging ===")

    from src.x_delegate import GhostDelegate, AUDIT_LOG_FILE

    # Clean up any existing audit log
    if AUDIT_LOG_FILE.exists():
        AUDIT_LOG_FILE.unlink()

    delegate = GhostDelegate()

    # Write some audit log entries
    delegate._audit_log("test_action", {"detail": "test_detail"})
    delegate._audit_log("another_action", {"key": "value", "count": 42})

    # Verify file was created
    assert AUDIT_LOG_FILE.exists(), "Audit log file should exist"

    # Read and verify contents
    with open(AUDIT_LOG_FILE, "r") as f:
        lines = f.readlines()
        assert len(lines) == 2, "Should have 2 log entries"

    logger.info("✓ Audit log file created")
    logger.info("✓ Audit entries written successfully")
    logger.info(f"✓ Log location: {AUDIT_LOG_FILE.absolute()}")

    # Clean up
    AUDIT_LOG_FILE.unlink()
    logger.info("Audit logging tests passed!")


async def test_kill_switch_prevents_operations():
    """Test that kill switch prevents all operations."""
    logger.info("\n=== Testing Kill Switch Prevention ===")

    from src.x_delegate import GhostDelegate

    delegate = GhostDelegate()
    delegate._kill_switch = True

    # Test login prevention
    result = await delegate.login_dummy()
    assert result is False, "Login should fail when kill switch is active"
    logger.info("✓ Login blocked by kill switch")

    # Test context manager prevention
    delegate._is_authenticated = True
    delegate.client = MagicMock()

    try:
        async with delegate.as_main():
            pass
        assert False, "Should have raised RuntimeError"
    except RuntimeError as e:
        assert "kill switch" in str(e).lower()
        logger.info("✓ Context manager blocked by kill switch")

    logger.info("Kill switch prevention tests passed!")


async def main():
    """Run all tests."""
    logger.info("=" * 70)
    logger.info("Ghost Delegate Security Hardening Test Suite")
    logger.info("=" * 70)

    try:
        await test_session_validation()
        await test_emergency_stop()
        await test_context_manager()
        await test_audit_logging()
        await test_kill_switch_prevents_operations()

        logger.info("\n" + "=" * 70)
        logger.info("ALL TESTS PASSED!")
        logger.info("=" * 70)

    except Exception as e:
        logger.error(f"\n{'=' * 70}")
        logger.error(f"TEST FAILED: {e}")
        logger.error("=" * 70)
        raise


if __name__ == "__main__":
    asyncio.run(main())
