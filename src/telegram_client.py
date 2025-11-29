"""
Telegram Client - Notifications and approval flow.

This module handles all Telegram interactions including:
- Sending approval requests for new tweets
- Processing approve/reject/edit callbacks
- Queue management commands
- Status notifications
- Error alerts (T017-S7)

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

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

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

        # Database reference for commands (injected via set_database)
        self._db = None

        # Callbacks for approval actions
        self._on_approve: Optional[Callable] = None
        self._on_reject: Optional[Callable] = None
        self._on_edit: Optional[Callable] = None

    def set_database(self, db) -> None:
        """Inject database for /queue and /stats commands."""
        self._db = db

    async def initialize(self) -> None:
        """Initialize the Telegram bot application."""
        self.app = Application.builder().token(self.token).build()

        # Register handlers
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("queue", self._cmd_queue))
        self.app.add_handler(CommandHandler("stats", self._cmd_stats))
        self.app.add_handler(CommandHandler("add_target", self._cmd_add_target))
        self.app.add_handler(CommandHandler("remove_target", self._cmd_remove_target))
        self.app.add_handler(CommandHandler("list_targets", self._cmd_list_targets))
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

    async def send_error_alert(
        self,
        error_type: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Send critical error alert to Telegram (T017-S7).

        This is used for alerting on:
        - Circuit breaker opened
        - Multiple consecutive failures
        - Rate limit exceeded
        - Service degradation

        Args:
            error_type: Type of error (e.g., "circuit_breaker_open")
            message: Human-readable error message
            details: Optional dictionary with additional error details
        """
        try:
            # Format timestamp
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Build alert message
            alert_lines = [
                "🚨 *CRITICAL ALERT*",
                "",
                f"*Type:* `{error_type}`",
                f"*Time:* {timestamp}",
                f"*Message:* {message}",
            ]

            # Add details if provided
            if details:
                alert_lines.append("")
                alert_lines.append("*Details:*")
                for key, value in details.items():
                    # Format value safely
                    if isinstance(value, (dict, list)):
                        value_str = json.dumps(value, indent=2)
                        alert_lines.append(f"```\n{key}: {value_str}\n```")
                    else:
                        alert_lines.append(f"  • {key}: `{value}`")

            alert_text = "\n".join(alert_lines)

            # Send alert
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=alert_text,
                parse_mode="Markdown",
            )

            logger.info(f"Sent error alert: {error_type}")

        except Exception as e:
            # Log but don't raise - we don't want error alerts to cause cascading failures
            logger.error(f"Failed to send error alert: {e}")

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
            "/stats - View statistics\n"
            "/list_targets - Show monitored accounts\n"
            "/add_target @a, @b - Add accounts to monitor\n"
            "/remove_target @a, @b - Stop monitoring accounts"
        )

    async def _cmd_queue(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /queue command - show pending tweets."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            pending = await self._db.get_pending_tweets()

            if not pending:
                await update.message.reply_text("📋 Queue is empty - no pending tweets")
                return

            # Format queue message
            lines = ["📋 *Pending Tweets*\n"]
            for i, tweet in enumerate(pending[:10], 1):  # Show max 10
                author = tweet.get("target_author", "unknown")
                reply_preview = tweet.get("reply_text", "")[:50] + "..."
                scheduled = tweet.get("scheduled_at", "Not scheduled")
                status = tweet.get("status", "pending")

                lines.append(
                    f"{i}. *@{author}*\n"
                    f"   Reply: _{reply_preview}_\n"
                    f"   Status: {status}\n"
                )

            if len(pending) > 10:
                lines.append(f"\n_...and {len(pending) - 10} more_")

            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="Markdown",
            )

        except Exception as e:
            logger.error(f"Error fetching queue: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_stats(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /stats command - show bot statistics."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            pending_count = await self._db.get_pending_count()
            posted_today = await self._db.get_posted_today_count()

            # Burst Mode status
            burst_status = "Enabled" if settings.burst_mode_enabled else "Disabled"
            quiet_hours = f"{settings.quiet_hours_start:02d}:00 - {settings.quiet_hours_end:02d}:00"

            message = (
                f"📊 *Bot Statistics*\n\n"
                f"*Queue:*\n"
                f"  Pending: {pending_count}\n"
                f"  Posted today: {posted_today}\n\n"
                f"*Burst Mode:* {burst_status}\n"
                f"  Quiet hours: {quiet_hours}\n"
                f"  Delay: {settings.min_delay_minutes}-{settings.max_delay_minutes} min\n\n"
                f"*Account:* @{settings.main_account_handle}"
            )

            await update.message.reply_text(message, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error fetching stats: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_add_target(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /add_target @handle1, @handle2 - add accounts to monitor."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /add_target @handle1, @handle2, ..."
            )
            return

        try:
            # Join args and split by comma to support: /add_target @a, @b, @c
            raw_input = " ".join(context.args)
            handles = [
                h.strip().lstrip("@").lower()
                for h in raw_input.split(",")
                if h.strip()
            ]

            added, reenabled, already_active = [], [], []
            for handle in handles:
                if handle and len(handle) >= 1:
                    status = await self._db.add_target_account(handle)
                    if status == "added":
                        added.append(f"@{handle}")
                    elif status == "re-enabled":
                        reenabled.append(f"@{handle}")
                    else:  # already_active
                        already_active.append(f"@{handle}")

            # Build response
            lines = []
            if added:
                lines.append(f"✅ Added: {', '.join(added)}")
            if reenabled:
                lines.append(f"🔄 Re-enabled: {', '.join(reenabled)}")
            if already_active:
                lines.append(f"ℹ️ Already active: {', '.join(already_active)}")

            if lines:
                await update.message.reply_text("\n".join(lines))
            else:
                await update.message.reply_text("❌ No valid handles provided")

        except Exception as e:
            logger.error(f"Error adding target: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_remove_target(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /remove_target @handle1, @handle2 - stop monitoring accounts."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /remove_target @handle1, @handle2, ..."
            )
            return

        try:
            # Join args and split by comma to support: /remove_target @a, @b, @c
            raw_input = " ".join(context.args)
            handles = [
                h.strip().lstrip("@").lower()
                for h in raw_input.split(",")
                if h.strip()
            ]

            removed = []
            for handle in handles:
                if handle and len(handle) >= 1:
                    await self._db.remove_target_account(handle)
                    removed.append(f"@{handle}")

            if removed:
                await update.message.reply_text(f"✅ Removed: {', '.join(removed)}")
            else:
                await update.message.reply_text("❌ No valid handles provided")

        except Exception as e:
            logger.error(f"Error removing target: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_list_targets(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /list_targets - show monitored accounts."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            targets = await self._db.get_target_accounts()
            if not targets:
                await update.message.reply_text("No targets configured")
                return

            text = "📋 *Monitored accounts:*\n" + "\n".join(
                f"  • @{h}" for h in targets
            )
            await update.message.reply_text(text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error listing targets: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

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

        elif action == "edit":
            # Edit interface deferred for MVP
            await query.message.reply_text(
                "✏️ Edit feature coming soon!\n\n"
                "For now, please reject and wait for a new suggestion."
            )

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
