"""
Ghost Delegate - Secure credential management for Twitter/X.

This module implements a security layer that protects the main account's
credentials by using a delegate (dummy) account for authentication.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │                    GHOST DELEGATE                           │
    ├─────────────────────────────────────────────────────────────┤
    │  Dummy Account:                                             │
    │  - Stores credentials (username, password)                  │
    │  - Performs login and authentication                        │
    │  - Takes the risk of rate limits / bans                    │
    │                                                             │
    │  Main Account:                                              │
    │  - Password NEVER stored                                    │
    │  - Only publishes via delegation                           │
    │  - Protected from direct exposure                          │
    └─────────────────────────────────────────────────────────────┘

Flow:
    1. Login with Dummy account credentials
    2. Use Twikit's set_delegate_account() to switch context to Main
    3. Publish tweet as Main
    4. Revert context back to Dummy (set_delegate_account(None))

Security Benefits:
    - If Dummy is banned → Create new dummy in 5 minutes
    - Main account password is never at risk
    - Instant revocation possible via X.com settings

Requirements:
    - Main account must have delegation enabled for Dummy
    - Configure delegation at: X.com → Settings → Account → Delegate
"""

import asyncio
import json
import logging
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional, Callable, TYPE_CHECKING

from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from src.database import Database

from twikit import Client
from twikit.errors import (
    BadRequest,
    Forbidden,
    TooManyRequests,
    TwitterException,
    Unauthorized,
)

from config import settings
from src.rate_limiter import RateLimiter, RateLimitExceeded

logger = logging.getLogger(__name__)

# Cookie file for session persistence
COOKIE_FILE = Path("cookies.json")

# Audit log file for security tracking
AUDIT_LOG_FILE = Path("ghost_delegate_audit.log")


class SessionHealth(Enum):
    """Session health status enumeration."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # Session works but may need refresh soon
    EXPIRED = "expired"    # Session expired, needs re-login
    FAILED = "failed"      # Cannot recover, needs manual intervention
    UNKNOWN = "unknown"


class GhostDelegate:
    """
    Manages secure account delegation for Twitter/X operations.

    This class handles authentication with the dummy account and
    context switching to the main account for publishing.
    """

    def __init__(self) -> None:
        """Initialize the Ghost Delegate with a Twikit client."""
        self.client: Optional[Client] = None
        self.dummy_user = None
        self.main_user = None
        self._is_authenticated = False
        self._kill_switch = False
        self._switch_timeout = settings.ghost_delegate_switch_timeout
        self._current_account = "none"  # Track current context

        # Session health tracking (T021)
        self._session_health = SessionHealth.UNKNOWN
        self._last_health_check: Optional[datetime] = None
        self._last_successful_operation: Optional[datetime] = None
        self._consecutive_failures = 0
        self._max_retry_attempts = 3
        self._health_check_interval = timedelta(minutes=5)

        # Callback for alerting (set by bot.py)
        self._on_session_alert: Optional[Callable] = None

        # Initialize rate limiter
        self.rate_limiter = RateLimiter(
            max_per_hour=settings.max_posts_per_hour,
            max_per_day=settings.max_posts_per_day,
            warning_threshold=settings.rate_limit_warning_threshold,
        )

    def _audit_log(self, action: str, details: dict) -> None:
        """
        Write structured audit log entry for security tracking.

        Args:
            action: The action being performed (e.g., "account_switch", "post_attempt")
            details: Dictionary of additional details to log
        """
        try:
            log_entry = {
                "timestamp": datetime.utcnow().isoformat(),
                "action": action,
                "current_account": self._current_account,
                **details,
            }

            # Append to audit log file
            with open(AUDIT_LOG_FILE, "a") as f:
                f.write(json.dumps(log_entry) + "\n")

            # Also log to standard logger
            logger.info(f"AUDIT: {action} - {json.dumps(details)}")
        except Exception as e:
            logger.error(f"Failed to write audit log: {e}")

    def _get_fernet(self) -> Optional[Fernet]:
        """
        Get Fernet instance for cookie encryption/decryption.

        Returns:
            Fernet instance if encryption key is configured, None otherwise.
        """
        if not settings.cookie_encryption_key:
            return None
        try:
            return Fernet(settings.cookie_encryption_key.encode())
        except Exception as e:
            logger.error(f"Invalid encryption key format: {e}")
            return None

    def _save_cookies_encrypted(self) -> bool:
        """
        Save cookies to file with encryption.

        The method:
        1. Saves cookies to a temp file (using Twikit's native method)
        2. Reads the temp file
        3. Encrypts the content
        4. Writes encrypted content to the actual cookie file

        Returns:
            True if successful, False otherwise.
        """
        if not self.client:
            return False

        fernet = self._get_fernet()
        if not fernet:
            # Fallback to plaintext if no encryption key (with warning)
            logger.warning("SECURITY WARNING: Saving cookies without encryption - set COOKIE_ENCRYPTION_KEY")
            self.client.save_cookies(str(COOKIE_FILE))
            return True

        try:
            # Save to temp file first
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
                tmp_path = tmp.name

            self.client.save_cookies(tmp_path)

            # Read plaintext cookies
            with open(tmp_path, 'r') as f:
                plaintext = f.read()

            # Encrypt and save to actual file
            encrypted = fernet.encrypt(plaintext.encode())
            with open(COOKIE_FILE, 'wb') as f:
                f.write(encrypted)

            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

            logger.debug("Saved encrypted cookies")
            return True

        except Exception as e:
            logger.error(f"Failed to save encrypted cookies: {e}")
            # Clean up temp file on error
            if 'tmp_path' in locals():
                Path(tmp_path).unlink(missing_ok=True)
            return False

    def _load_cookies_encrypted(self) -> bool:
        """
        Load cookies from encrypted file.

        The method:
        1. Reads encrypted content from cookie file
        2. Decrypts the content
        3. Writes to a temp file
        4. Loads into client using Twikit's native method

        Returns:
            True if successful, False otherwise.
        """
        if not self.client:
            return False

        if not COOKIE_FILE.exists():
            return False

        fernet = self._get_fernet()
        if not fernet:
            # Fallback to plaintext if no encryption key
            logger.warning("SECURITY WARNING: Loading cookies without encryption - set COOKIE_ENCRYPTION_KEY")
            self.client.load_cookies(str(COOKIE_FILE))
            return True

        try:
            # Read encrypted cookies
            with open(COOKIE_FILE, 'rb') as f:
                encrypted = f.read()

            # Try to decrypt
            try:
                plaintext = fernet.decrypt(encrypted).decode()
            except InvalidToken:
                # File might be plaintext (migration case)
                logger.info("Cookie file appears to be plaintext, attempting migration...")
                with open(COOKIE_FILE, 'r') as f:
                    plaintext = f.read()
                # Validate it's valid JSON
                json.loads(plaintext)
                logger.info("Plaintext cookies loaded, will be encrypted on next save")

            # Write to temp file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
                tmp.write(plaintext)
                tmp_path = tmp.name

            # Load into client
            self.client.load_cookies(tmp_path)

            # Clean up temp file
            Path(tmp_path).unlink(missing_ok=True)

            logger.debug("Loaded encrypted cookies")
            return True

        except json.JSONDecodeError:
            logger.error("Cookie file is corrupted - not valid JSON")
            return False
        except Exception as e:
            logger.error(f"Failed to load encrypted cookies: {e}")
            # Clean up temp file on error
            if 'tmp_path' in locals():
                Path(tmp_path).unlink(missing_ok=True)
            return False

    async def validate_session(self) -> bool:
        """
        Check if current session is still valid.

        Returns:
            True if session is valid and authenticated, False otherwise.
        """
        if not self._is_authenticated or not self.client:
            self._audit_log("session_validation", {
                "result": "failed",
                "reason": "not_authenticated"
            })
            return False

        if self._kill_switch:
            self._audit_log("session_validation", {
                "result": "failed",
                "reason": "kill_switch_active"
            })
            return False

        try:
            # Try to fetch dummy user info to verify session
            test_user = await self.client.get_user_by_screen_name(
                settings.dummy_username
            )

            if test_user is None:
                self._audit_log("session_validation", {
                    "result": "failed",
                    "reason": "session_expired"
                })
                self._is_authenticated = False
                return False

            self._audit_log("session_validation", {
                "result": "success"
            })
            return True

        except (Unauthorized, TwitterException) as e:
            logger.warning(f"Session validation failed: {e}")
            self._audit_log("session_validation", {
                "result": "failed",
                "reason": str(e),
                "error_type": type(e).__name__
            })
            self._is_authenticated = False
            return False
        except Exception as e:
            logger.error(f"Unexpected error during session validation: {e}")
            self._audit_log("session_validation", {
                "result": "error",
                "error": str(e)
            })
            return False

    @asynccontextmanager
    async def as_main(self):
        """
        Context manager for safe main account operations.

        Ensures proper account switching with guaranteed revert and timeout protection.

        Usage:
            async with ghost_delegate.as_main():
                # Perform operations as main account
                await tweet.reply(text)

        Raises:
            RuntimeError: If kill switch is active or delegate is disabled.
            asyncio.TimeoutError: If operation exceeds timeout.
        """
        if self._kill_switch:
            raise RuntimeError("Emergency kill switch is active - cannot switch to main account")

        if not settings.ghost_delegate_enabled:
            raise RuntimeError("Ghost delegate is disabled in configuration")

        if not self._is_authenticated:
            raise RuntimeError("Not authenticated - cannot switch to main account")

        # Validate session before switching
        if not await self.validate_session():
            raise RuntimeError("Session validation failed - re-authentication required")

        try:
            # Switch to main account
            self.client.set_delegate_account(self.main_user.id)
            self._current_account = "main"
            self._audit_log("account_switch", {
                "from": "dummy",
                "to": "main",
                "handle": settings.main_account_handle
            })
            logger.debug(f"Switched to main account: @{settings.main_account_handle}")

            # Yield with timeout protection
            try:
                yield
            except asyncio.TimeoutError:
                logger.error(f"Operation timed out after {self._switch_timeout}s")
                self._audit_log("operation_timeout", {
                    "timeout": self._switch_timeout,
                    "account": "main"
                })
                raise

        finally:
            # GUARANTEED revert - always executed even on exceptions
            await self._revert_to_dummy()

    async def emergency_stop(self) -> None:
        """
        Emergency shutdown - immediately clear all sessions and prevent further operations.

        This method:
        1. Sets the kill switch flag to prevent any further operations
        2. Reverts to dummy account if currently switched
        3. Clears all cookies and session data
        4. Marks the instance as unauthenticated

        This can be triggered manually or via external control (e.g., Telegram command).
        """
        logger.warning("EMERGENCY STOP TRIGGERED - Shutting down Ghost Delegate")
        self._audit_log("emergency_stop", {
            "triggered_at": datetime.utcnow().isoformat(),
            "previous_state": "authenticated" if self._is_authenticated else "unauthenticated"
        })

        try:
            # Set kill switch immediately
            self._kill_switch = True

            # Revert to dummy if currently switched
            if self._current_account == "main":
                await self._revert_to_dummy()

            # Clear cookies
            if COOKIE_FILE.exists():
                COOKIE_FILE.unlink()
                logger.info("Deleted cookie file")

            # Clear client session
            if self.client:
                self.client = None

            # Mark as unauthenticated
            self._is_authenticated = False
            self._current_account = "none"

            logger.warning("Emergency stop completed - all sessions cleared")
            self._audit_log("emergency_stop_complete", {
                "status": "success"
            })

        except Exception as e:
            logger.error(f"Error during emergency stop: {e}")
            self._audit_log("emergency_stop_error", {
                "error": str(e)
            })
            # Even if there's an error, ensure kill switch is set
            self._kill_switch = True
            raise

    async def login_dummy(self, db: Optional["Database"] = None) -> bool:
        """
        Authenticate with the dummy account.

        Attempts to load existing cookies first for faster startup.
        Falls back to fresh login if cookies are invalid or missing.

        Includes login cooldown protection to prevent account bans:
        - Tracks login attempts in the database
        - Enforces a 3-hour (configurable) cooldown between fresh logins
        - Waits if cooldown is active

        Args:
            db: Optional database instance for login tracking and cooldown enforcement.

        Returns:
            True if login successful, False otherwise.
        """
        # Check if disabled
        if not settings.ghost_delegate_enabled:
            logger.error("Ghost delegate is disabled in configuration")
            self._audit_log("login_attempt", {
                "result": "failed",
                "reason": "delegate_disabled"
            })
            return False

        # Check kill switch
        if self._kill_switch:
            logger.error("Cannot login - emergency kill switch is active")
            self._audit_log("login_attempt", {
                "result": "failed",
                "reason": "kill_switch_active"
            })
            return False

        cookies_existed = COOKIE_FILE.exists()

        try:
            self.client = Client()

            # Try to load existing cookies first (with encryption)
            if cookies_existed:
                try:
                    self._load_cookies_encrypted()
                    logger.info("Loaded cookies from file (encrypted)")

                    # Verify session is still valid by fetching user info
                    self.dummy_user = await self.client.get_user_by_screen_name(
                        settings.dummy_username
                    )
                    self.main_user = await self.client.get_user_by_screen_name(
                        settings.main_account_handle
                    )

                    self._is_authenticated = True
                    self._current_account = "dummy"
                    self._session_health = SessionHealth.HEALTHY
                    self._last_successful_operation = datetime.utcnow()
                    self._consecutive_failures = 0
                    logger.info(f"Session restored for dummy: @{settings.dummy_username}")
                    self._audit_log("login_success", {
                        "method": "cookie_restore",
                        "username": settings.dummy_username
                    })

                    # Record successful cookie restore in database
                    if db:
                        try:
                            await db.record_login_attempt(
                                account_type="dummy",
                                login_type="cookie_restore",
                                success=True,
                                cookies_existed=True,
                                cookies_valid=True,
                            )
                        except Exception as e:
                            logger.warning(f"Failed to record login attempt (DB): {e}")

                    return True

                except Exception as e:
                    logger.warning(f"Cookies invalid or expired, doing fresh login: {e}")
                    self._audit_log("cookie_restore_failed", {
                        "reason": str(e),
                        "fallback": "fresh_login"
                    })

                    # Record failed cookie restore in database
                    if db:
                        try:
                            await db.record_login_attempt(
                                account_type="dummy",
                                login_type="cookie_restore",
                                success=False,
                                error_message=str(e),
                                error_type=type(e).__name__,
                                cookies_existed=True,
                                cookies_valid=False,
                            )
                        except Exception as db_e:
                            logger.warning(f"Failed to record login attempt (DB): {db_e}")

            # =========================================================
            # FRESH LOGIN REQUIRED - Check cooldown before proceeding
            # =========================================================
            if db and settings.login_cooldown_enabled:
                try:
                    cooldown_remaining = await db.get_login_cooldown_remaining(
                        account_type="dummy",
                        cooldown_hours=settings.login_cooldown_hours,
                    )

                    if cooldown_remaining > 0:
                        hours = cooldown_remaining // 3600
                        minutes = (cooldown_remaining % 3600) // 60
                        logger.warning(
                            f"Login cooldown active. Waiting {hours}h {minutes}m "
                            f"({cooldown_remaining}s) before fresh login..."
                        )
                        self._audit_log("login_cooldown_wait", {
                            "wait_seconds": cooldown_remaining,
                            "cooldown_hours": settings.login_cooldown_hours
                        })

                        # Wait for cooldown to expire
                        await asyncio.sleep(cooldown_remaining)
                        logger.info("Login cooldown expired, proceeding with fresh login")

                except Exception as e:
                    logger.warning(f"Failed to check login cooldown (proceeding anyway): {e}")

            # Fresh login
            await self.client.login(
                auth_info_1=settings.dummy_username,
                auth_info_2=settings.dummy_email,
                password=settings.dummy_password,
            )

            # Save cookies for next time (with encryption)
            self._save_cookies_encrypted()
            logger.info("Saved cookies to file (encrypted)")

            # Get user objects for context switching
            self.dummy_user = await self.client.get_user_by_screen_name(
                settings.dummy_username
            )
            self.main_user = await self.client.get_user_by_screen_name(
                settings.main_account_handle
            )

            self._is_authenticated = True
            self._current_account = "dummy"
            self._session_health = SessionHealth.HEALTHY
            self._last_successful_operation = datetime.utcnow()
            self._consecutive_failures = 0
            logger.info(f"Logged in as dummy: @{settings.dummy_username}")
            self._audit_log("login_success", {
                "method": "fresh_login",
                "username": settings.dummy_username
            })

            # Record successful fresh login in database
            if db:
                try:
                    await db.record_login_attempt(
                        account_type="dummy",
                        login_type="fresh",
                        success=True,
                        cookies_existed=cookies_existed,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record login attempt (DB): {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to login as dummy: {e}")
            self._is_authenticated = False
            self._session_health = SessionHealth.FAILED
            self._audit_log("login_failed", {
                "error": str(e),
                "error_type": type(e).__name__
            })

            # Record failed fresh login in database
            if db:
                try:
                    await db.record_login_attempt(
                        account_type="dummy",
                        login_type="fresh",
                        success=False,
                        error_message=str(e),
                        error_type=type(e).__name__,
                        cookies_existed=cookies_existed,
                    )
                except Exception as db_e:
                    logger.warning(f"Failed to record login attempt (DB): {db_e}")

            return False

    async def post_as_main(self, tweet_id: str, reply_text: str) -> bool:
        """
        Post a reply as the main account using delegation.

        This method now includes:
        - Session validation before posting
        - Rate limiting checks
        - Guaranteed account revert via context manager
        - Comprehensive audit logging

        Args:
            tweet_id: The ID of the tweet to reply to.
            reply_text: The text content of the reply.

        Returns:
            True if post successful, False otherwise.
        """
        # Pre-flight checks
        if not self._is_authenticated:
            logger.error("Cannot post: Not authenticated")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "not_authenticated",
                "tweet_id": tweet_id
            })
            return False

        # Validate session before attempting post
        if not await self.validate_session():
            logger.error("Cannot post: Session validation failed")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "session_invalid",
                "tweet_id": tweet_id
            })
            return False

        # Check rate limits
        if not await self.rate_limiter.can_post():
            wait_time = self.rate_limiter.get_wait_time()
            logger.warning(
                f"Rate limit exceeded. Wait {wait_time}s ({wait_time // 60}m) before next post."
            )
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "rate_limit_exceeded",
                "tweet_id": tweet_id,
                "wait_time_seconds": wait_time
            })
            return False

        # Use context manager for safe account switching
        try:
            async with asyncio.timeout(self._switch_timeout):
                async with self.as_main():
                    # Get the tweet and reply
                    tweet = await self.client.get_tweet_by_id(tweet_id)
                    await tweet.reply(reply_text)

                    # Record successful post
                    await self.rate_limiter.record_post()

                    logger.info(f"Posted reply as @{settings.main_account_handle}")
                    self._audit_log("post_success", {
                        "tweet_id": tweet_id,
                        "reply_length": len(reply_text),
                        "handle": settings.main_account_handle
                    })
                    return True

        except asyncio.TimeoutError:
            logger.error(f"Post operation timed out after {self._switch_timeout}s")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "timeout",
                "tweet_id": tweet_id,
                "timeout": self._switch_timeout
            })
            return False

        except TooManyRequests as e:
            logger.error(f"Rate limited by Twitter - try again later: {e}")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "twitter_rate_limit",
                "tweet_id": tweet_id,
                "error": str(e)
            })
            return False

        except Unauthorized as e:
            logger.error(f"Authentication failed - session may have expired: {e}")
            self._is_authenticated = False
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "unauthorized",
                "tweet_id": tweet_id,
                "error": str(e)
            })
            return False

        except Forbidden as e:
            logger.error(f"Permission denied - check delegation settings: {e}")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "forbidden",
                "tweet_id": tweet_id,
                "error": str(e)
            })
            return False

        except BadRequest as e:
            # Could be duplicate tweet or invalid content
            error_msg = str(e).lower()
            if "duplicate" in error_msg:
                logger.warning(f"Duplicate tweet detected for tweet_id={tweet_id}")
                self._audit_log("post_attempt", {
                    "result": "failed",
                    "reason": "duplicate",
                    "tweet_id": tweet_id
                })
            else:
                logger.error(f"Bad request - invalid content or parameters: {e}")
                self._audit_log("post_attempt", {
                    "result": "failed",
                    "reason": "bad_request",
                    "tweet_id": tweet_id,
                    "error": str(e)
                })
            return False

        except TwitterException as e:
            logger.error(f"Twitter API error: {e}")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "twitter_api_error",
                "tweet_id": tweet_id,
                "error": str(e),
                "error_type": type(e).__name__
            })
            return False

        except RuntimeError as e:
            # Catch errors from as_main() context manager
            logger.error(f"Runtime error: {e}")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "runtime_error",
                "tweet_id": tweet_id,
                "error": str(e)
            })
            return False

        except Exception as e:
            logger.error(f"Unexpected error posting as main: {e}")
            self._audit_log("post_attempt", {
                "result": "failed",
                "reason": "unexpected_error",
                "tweet_id": tweet_id,
                "error": str(e),
                "error_type": type(e).__name__
            })
            return False

    async def _revert_to_dummy(self) -> None:
        """
        Revert context back to the dummy account by clearing delegation.

        This method is CRITICAL for security - it ensures the main account
        is never left in an active state where it could be used accidentally.
        """
        try:
            if self.client:
                self.client.set_delegate_account(None)
                previous_account = self._current_account
                self._current_account = "dummy"

                logger.debug(f"Reverted to dummy: @{settings.dummy_username}")
                self._audit_log("account_revert", {
                    "from": previous_account,
                    "to": "dummy"
                })
        except Exception as e:
            logger.error(f"Failed to revert to dummy: {e}")
            self._audit_log("account_revert_error", {
                "error": str(e),
                "critical": True
            })

    async def get_rate_limit_status(self) -> dict:
        """
        Get current rate limit status.

        Returns:
            Dictionary with usage statistics including:
            - hourly_used, hourly_limit, hourly_remaining, hourly_percentage
            - daily_used, daily_limit, daily_remaining, daily_percentage
            - can_post, wait_time_seconds
        """
        return await self.rate_limiter.get_status()

    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._is_authenticated

    @property
    def session_health(self) -> SessionHealth:
        """Get current session health status."""
        return self._session_health

    def set_session_alert_callback(self, callback: Callable) -> None:
        """
        Register callback for session alerts (T021-S4).

        Args:
            callback: Async function to call when session needs attention.
                      Signature: async def callback(alert_type: str, message: str, details: dict)
        """
        self._on_session_alert = callback
        logger.debug("Session alert callback registered")

    async def _send_session_alert(self, alert_type: str, message: str, details: dict) -> None:
        """
        Send session alert via registered callback.

        Args:
            alert_type: Type of alert (e.g., "session_expired", "auth_failed")
            message: Human-readable message
            details: Additional details for the alert
        """
        if self._on_session_alert:
            try:
                await self._on_session_alert(alert_type, message, details)
            except Exception as e:
                logger.error(f"Failed to send session alert: {e}")

    async def refresh_session(self, db: Optional["Database"] = None) -> bool:
        """
        Attempt to refresh the session by re-authenticating (T021-S3).

        This method:
        1. Clears existing cookies
        2. Performs fresh login (with cooldown check if db provided)
        3. Updates session health status

        Args:
            db: Optional database instance for login tracking and cooldown enforcement.

        Returns:
            True if refresh successful, False otherwise.
        """
        logger.info("Attempting session refresh...")
        self._audit_log("session_refresh_attempt", {
            "previous_health": self._session_health.value,
            "consecutive_failures": self._consecutive_failures
        })

        try:
            # Clear existing cookies to force fresh login
            if COOKIE_FILE.exists():
                COOKIE_FILE.unlink()
                logger.info("Cleared existing cookies for fresh login")

            # Reset client
            self.client = None
            self._is_authenticated = False

            # Attempt fresh login (with cooldown check if db provided)
            success = await self.login_dummy(db=db)

            if success:
                self._session_health = SessionHealth.HEALTHY
                self._consecutive_failures = 0
                self._last_successful_operation = datetime.utcnow()
                self._audit_log("session_refresh_success", {
                    "new_health": self._session_health.value
                })
                logger.info("Session refresh successful")
                return True
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self._max_retry_attempts:
                    self._session_health = SessionHealth.FAILED
                    await self._send_session_alert(
                        "session_refresh_failed",
                        "Session refresh failed after maximum retries - manual intervention required",
                        {
                            "consecutive_failures": self._consecutive_failures,
                            "max_retries": self._max_retry_attempts
                        }
                    )
                else:
                    self._session_health = SessionHealth.EXPIRED

                self._audit_log("session_refresh_failed", {
                    "consecutive_failures": self._consecutive_failures,
                    "new_health": self._session_health.value
                })
                logger.error(f"Session refresh failed (attempt {self._consecutive_failures})")
                return False

        except Exception as e:
            self._consecutive_failures += 1
            self._session_health = SessionHealth.FAILED
            logger.error(f"Session refresh error: {e}")
            self._audit_log("session_refresh_error", {
                "error": str(e),
                "consecutive_failures": self._consecutive_failures
            })

            await self._send_session_alert(
                "session_refresh_error",
                f"Session refresh encountered an error: {e}",
                {"error": str(e), "error_type": type(e).__name__}
            )
            return False

    async def check_session_health(
        self,
        auto_refresh: bool = True,
        db: Optional["Database"] = None,
    ) -> SessionHealth:
        """
        Perform comprehensive session health check (T021-S2).

        This method:
        1. Validates the current session
        2. Updates health status
        3. Optionally attempts auto-refresh if expired (with cooldown check if db provided)

        Args:
            auto_refresh: If True, attempt to refresh expired sessions automatically.
            db: Optional database instance for login tracking and cooldown enforcement.

        Returns:
            Current SessionHealth status.
        """
        self._last_health_check = datetime.utcnow()

        # If kill switch is active, session is failed
        if self._kill_switch:
            self._session_health = SessionHealth.FAILED
            self._audit_log("health_check", {
                "result": "failed",
                "reason": "kill_switch_active"
            })
            return self._session_health

        # If not authenticated, session is expired
        if not self._is_authenticated or not self.client:
            self._session_health = SessionHealth.EXPIRED
            self._audit_log("health_check", {
                "result": "expired",
                "reason": "not_authenticated"
            })

            if auto_refresh:
                logger.info("Session expired, attempting auto-refresh...")
                if await self.refresh_session(db=db):
                    return self._session_health
                else:
                    await self._send_session_alert(
                        "session_expired",
                        "Session expired and auto-refresh failed",
                        {"auto_refresh_attempted": True}
                    )

            return self._session_health

        # Validate session by making a test request
        try:
            test_user = await self.client.get_user_by_screen_name(settings.dummy_username)

            if test_user is None:
                self._session_health = SessionHealth.EXPIRED
                self._audit_log("health_check", {
                    "result": "expired",
                    "reason": "user_fetch_returned_none"
                })

                if auto_refresh:
                    await self.refresh_session(db=db)

                return self._session_health

            # Session is healthy
            self._session_health = SessionHealth.HEALTHY
            self._consecutive_failures = 0
            self._last_successful_operation = datetime.utcnow()
            self._audit_log("health_check", {
                "result": "healthy"
            })
            return self._session_health

        except Unauthorized as e:
            logger.warning(f"Session unauthorized during health check: {e}")
            self._session_health = SessionHealth.EXPIRED
            self._is_authenticated = False
            self._audit_log("health_check", {
                "result": "expired",
                "reason": "unauthorized",
                "error": str(e)
            })

            if auto_refresh:
                await self.refresh_session(db=db)

            return self._session_health

        except TooManyRequests as e:
            # Rate limited but session might still be valid
            logger.warning(f"Rate limited during health check: {e}")
            self._session_health = SessionHealth.DEGRADED
            self._audit_log("health_check", {
                "result": "degraded",
                "reason": "rate_limited",
                "error": str(e)
            })
            return self._session_health

        except TwitterException as e:
            logger.error(f"Twitter error during health check: {e}")
            self._consecutive_failures += 1

            if self._consecutive_failures >= 3:
                self._session_health = SessionHealth.EXPIRED

                if auto_refresh:
                    await self.refresh_session(db=db)
            else:
                self._session_health = SessionHealth.DEGRADED

            self._audit_log("health_check", {
                "result": self._session_health.value,
                "reason": "twitter_error",
                "error": str(e),
                "consecutive_failures": self._consecutive_failures
            })
            return self._session_health

        except Exception as e:
            logger.error(f"Unexpected error during health check: {e}")
            self._session_health = SessionHealth.UNKNOWN
            self._audit_log("health_check", {
                "result": "unknown",
                "reason": "unexpected_error",
                "error": str(e)
            })
            return self._session_health

    def get_session_status(self) -> dict:
        """
        Get comprehensive session status for monitoring (T021).

        Returns:
            Dictionary with session health information.
        """
        now = datetime.utcnow()

        # Calculate time since last health check
        time_since_check = None
        if self._last_health_check:
            time_since_check = (now - self._last_health_check).total_seconds()

        # Calculate time since last successful operation
        time_since_success = None
        if self._last_successful_operation:
            time_since_success = (now - self._last_successful_operation).total_seconds()

        return {
            "health": self._session_health.value,
            "is_authenticated": self._is_authenticated,
            "current_account": self._current_account,
            "kill_switch_active": self._kill_switch,
            "consecutive_failures": self._consecutive_failures,
            "last_health_check": self._last_health_check.isoformat() if self._last_health_check else None,
            "seconds_since_health_check": time_since_check,
            "last_successful_operation": self._last_successful_operation.isoformat() if self._last_successful_operation else None,
            "seconds_since_success": time_since_success,
            "needs_health_check": time_since_check is None or time_since_check > self._health_check_interval.total_seconds(),
        }

    def is_session_healthy(self) -> bool:
        """
        Quick check if session is in a healthy state.

        Returns:
            True if session is HEALTHY or DEGRADED (operational), False otherwise.
        """
        return self._session_health in (SessionHealth.HEALTHY, SessionHealth.DEGRADED)
