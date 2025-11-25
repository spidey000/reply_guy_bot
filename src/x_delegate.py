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

import logging
from pathlib import Path
from typing import Optional

from twikit import Client
from twikit.errors import (
    BadRequest,
    Forbidden,
    TooManyRequests,
    TwitterException,
    Unauthorized,
)

from config import settings

logger = logging.getLogger(__name__)

# Cookie file for session persistence
COOKIE_FILE = Path("cookies.json")


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

    async def login_dummy(self) -> bool:
        """
        Authenticate with the dummy account.

        Attempts to load existing cookies first for faster startup.
        Falls back to fresh login if cookies are invalid or missing.

        Returns:
            True if login successful, False otherwise.
        """
        try:
            self.client = Client()

            # Try to load existing cookies first
            if COOKIE_FILE.exists():
                try:
                    self.client.load_cookies(str(COOKIE_FILE))
                    logger.info("Loaded cookies from file")

                    # Verify session is still valid by fetching user info
                    self.dummy_user = await self.client.get_user_by_screen_name(
                        settings.dummy_username
                    )
                    self.main_user = await self.client.get_user_by_screen_name(
                        settings.main_account_handle
                    )

                    self._is_authenticated = True
                    logger.info(f"Session restored for dummy: @{settings.dummy_username}")
                    return True

                except Exception as e:
                    logger.warning(f"Cookies invalid or expired, doing fresh login: {e}")

            # Fresh login required
            await self.client.login(
                auth_info_1=settings.dummy_username,
                auth_info_2=settings.dummy_email,
                password=settings.dummy_password,
            )

            # Save cookies for next time
            self.client.save_cookies(str(COOKIE_FILE))
            logger.info("Saved cookies to file")

            # Get user objects for context switching
            self.dummy_user = await self.client.get_user_by_screen_name(
                settings.dummy_username
            )
            self.main_user = await self.client.get_user_by_screen_name(
                settings.main_account_handle
            )

            self._is_authenticated = True
            logger.info(f"Logged in as dummy: @{settings.dummy_username}")
            return True

        except Exception as e:
            logger.error(f"Failed to login as dummy: {e}")
            self._is_authenticated = False
            return False

    async def post_as_main(self, tweet_id: str, reply_text: str) -> bool:
        """
        Post a reply as the main account using delegation.

        Args:
            tweet_id: The ID of the tweet to reply to.
            reply_text: The text content of the reply.

        Returns:
            True if post successful, False otherwise.
        """
        if not self._is_authenticated:
            logger.error("Cannot post: Not authenticated")
            return False

        try:
            # Switch to main account context using delegation
            self.client.set_delegate_account(self.main_user.id)
            logger.debug(f"Switched to main account: @{settings.main_account_handle}")

            # Get the tweet and reply
            tweet = await self.client.get_tweet_by_id(tweet_id)
            await tweet.reply(reply_text)

            logger.info(f"Posted reply as @{settings.main_account_handle}")
            return True

        except TooManyRequests as e:
            logger.error(f"Rate limited by Twitter - try again later: {e}")
            return False

        except Unauthorized as e:
            logger.error(f"Authentication failed - session may have expired: {e}")
            self._is_authenticated = False
            return False

        except Forbidden as e:
            logger.error(f"Permission denied - check delegation settings: {e}")
            return False

        except BadRequest as e:
            # Could be duplicate tweet or invalid content
            error_msg = str(e).lower()
            if "duplicate" in error_msg:
                logger.warning(f"Duplicate tweet detected for tweet_id={tweet_id}")
            else:
                logger.error(f"Bad request - invalid content or parameters: {e}")
            return False

        except TwitterException as e:
            logger.error(f"Twitter API error: {e}")
            return False

        except Exception as e:
            logger.error(f"Unexpected error posting as main: {e}")
            return False

        finally:
            # Always revert to dummy account
            await self._revert_to_dummy()

    async def _revert_to_dummy(self) -> None:
        """Revert context back to the dummy account by clearing delegation."""
        try:
            if self.client:
                self.client.set_delegate_account(None)
                logger.debug(f"Reverted to dummy: @{settings.dummy_username}")
        except Exception as e:
            logger.error(f"Failed to revert to dummy: {e}")

    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._is_authenticated
