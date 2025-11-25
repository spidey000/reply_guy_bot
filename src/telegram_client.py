"""
Telegram Client - Notifications and approval flow.

This module handles all Telegram interactions including:
- Sending approval requests for new tweets
- Processing approve/reject/edit callbacks
- Queue management commands
- Status notifications

Bot Commands:
    /start - Initialize bot
    /queue - Show pending tweets queue
    /stats - Show bot statistics
    /pause - Pause tweet monitoring
    /resume - Resume tweet monitoring

Approval Flow:
    ┌─────────────────────────────────────────────────────────────┐
    │  New tweet detected                                         │
    │  ↓                                                          │
    │  Send Telegram message with:                               │
    │  - Original tweet preview                                  │
    │  - AI-generated reply                                      │
    │  - Buttons: [Approve] [Edit] [Reject]                     │
    │  ↓                                                          │
    │  User taps button                                          │
    │  ↓                                                          │
    │  Approve → Schedule via Burst Mode                         │
    │  Edit → Show edit interface → Then schedule               │
    │  Reject → Discard tweet                                    │
    └─────────────────────────────────────────────────────────────┘

Configuration:
    TELEGRAM_BOT_TOKEN: Bot token from @BotFather
    TELEGRAM_CHAT_ID: Your personal/group chat ID
"""

import logging
from typing import Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import settings

logger = logging.getLogger(__name__)


class TelegramClient:
    """
    Handles Telegram bot interactions for tweet approval flow.

    This client sends notifications and processes user responses
    for the tweet approval workflow.
    """

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        """
        Initialize the Telegram client.

        Args:
            token: Bot token. Defaults to config value.
            chat_id: Target chat ID. Defaults to config value.
        """
        self.token = token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.app: Optional[Application] = None

        # Callbacks for approval actions
        self._on_approve: Optional[Callable] = None
        self._on_reject: Optional[Callable] = None
        self._on_edit: Optional[Callable] = None

    async def initialize(self) -> None:
        """Initialize the Telegram bot application."""
        self.app = Application.builder().token(self.token).build()

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("queue", self._cmd_queue))
        self.app.add_handler(CommandHandler("stats", self._cmd_stats))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        logger.info("Telegram client initialized")

    async def send_approval_request(
        self,
        tweet_data: dict,
        suggested_reply: str,
    ) -> int:
        """
        Send a tweet approval request to Telegram.

        Args:
            tweet_data: Original tweet information.
            suggested_reply: AI-generated reply suggestion.

        Returns:
            Message ID of the sent message.
        """
        author = tweet_data.get("author", "unknown")
        content = tweet_data.get("content", "")
        tweet_id = tweet_data.get("id", "")

        message = (
            f"*New Tweet to Reply*\n\n"
            f"*@{author}:*\n{content}\n\n"
            f"*Suggested Reply:*\n{suggested_reply}"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{tweet_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{tweet_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{tweet_id}"),
            ]
        ])

        msg = await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )

        logger.info(f"Sent approval request for tweet {tweet_id}")
        return msg.message_id

    async def send_scheduled_confirmation(
        self,
        tweet_id: str,
        scheduled_time: str,
    ) -> None:
        """
        Send confirmation that a tweet was scheduled.

        Args:
            tweet_id: ID of the scheduled tweet.
            scheduled_time: Human-readable scheduled time.
        """
        message = (
            f"✅ *Scheduled*\n\n"
            f"Tweet will be posted {scheduled_time}\n"
            f"Account: @{settings.main_account_handle}"
        )

        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="Markdown",
        )

    async def send_published_notification(self, tweet: dict) -> None:
        """
        Send notification that a tweet was published.

        Args:
            tweet: Published tweet data.
        """
        message = f"📤 *Published*\n\nReply posted successfully!"

        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="Markdown",
        )

    # =========================================================================
    # Command Handlers
    # =========================================================================

    async def _cmd_start(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /start command."""
        await update.message.reply_text(
            "👋 Reply Guy Bot initialized!\n\n"
            "Commands:\n"
            "/queue - View pending tweets\n"
            "/stats - View statistics"
        )

    async def _cmd_queue(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /queue command."""
        # TODO: Implement queue display
        await update.message.reply_text("📋 Queue is empty")

    async def _cmd_stats(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /stats command."""
        # TODO: Implement stats display
        await update.message.reply_text("📊 Stats coming soon...")

    # =========================================================================
    # Callback Handler
    # =========================================================================

    async def _handle_callback(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle inline keyboard callbacks."""
        query = update.callback_query
        await query.answer()

        action, tweet_id = query.data.split(":", 1)

        if action == "approve" and self._on_approve:
            await self._on_approve(tweet_id)
            await query.edit_message_reply_markup(reply_markup=None)

        elif action == "reject" and self._on_reject:
            await self._on_reject(tweet_id)
            await query.edit_message_text("❌ Rejected")

        elif action == "edit" and self._on_edit:
            await self._on_edit(tweet_id)

    # =========================================================================
    # Callback Registration
    # =========================================================================

    def on_approve(self, callback: Callable) -> None:
        """Register callback for approve action."""
        self._on_approve = callback

    def on_reject(self, callback: Callable) -> None:
        """Register callback for reject action."""
        self._on_reject = callback

    def on_edit(self, callback: Callable) -> None:
        """Register callback for edit action."""
        self._on_edit = callback
