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
    2. Use Twikit's set_active_user() to switch context to Main
    3. Publish tweet as Main
    4. Revert context back to Dummy

Security Benefits:
    - If Dummy is banned → Create new dummy in 5 minutes
    - Main account password is never at risk
    - Instant revocation possible via X.com settings

Requirements:
    - Main account must have delegation enabled for Dummy
    - Configure delegation at: X.com → Settings → Account → Delegate
"""

import logging
from typing import Optional

from twikit import Client

from config import settings

logger = logging.getLogger(__name__)


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

        Returns:
            True if login successful, False otherwise.
        """
        try:
            self.client = Client()
            await self.client.login(
                auth_info_1=settings.dummy_username,
                auth_info_2=settings.dummy_email,
                password=settings.dummy_password,
            )

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
            # Switch to main account context
            self.client.set_active_user(self.main_user)
            logger.debug(f"Switched to main account: @{settings.main_account_handle}")

            # Get the tweet and reply
            tweet = await self.client.get_tweet_by_id(tweet_id)
            await tweet.reply(reply_text)

            logger.info(f"Posted reply as @{settings.main_account_handle}")
            return True

        except Exception as e:
            logger.error(f"Failed to post as main: {e}")
            return False

        finally:
            # Always revert to dummy account
            await self._revert_to_dummy()

    async def _revert_to_dummy(self) -> None:
        """Revert context back to the dummy account."""
        try:
            if self.client and self.dummy_user:
                self.client.set_active_user(self.dummy_user)
                logger.debug(f"Reverted to dummy: @{settings.dummy_username}")
        except Exception as e:
            logger.error(f"Failed to revert to dummy: {e}")

    @property
    def is_authenticated(self) -> bool:
        """Check if currently authenticated."""
        return self._is_authenticated
