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

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import settings, create_user_settings, SettingValidator

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
        self.app.add_handler(CommandHandler("settings", self._cmd_settings))
        # Search query commands
        self.app.add_handler(CommandHandler("add_search", self._cmd_add_search))
        self.app.add_handler(CommandHandler("remove_search", self._cmd_remove_search))
        self.app.add_handler(CommandHandler("list_searches", self._cmd_list_searches))
        # Topic commands
        self.app.add_handler(CommandHandler("add_topic", self._cmd_add_topic))
        self.app.add_handler(CommandHandler("remove_topic", self._cmd_remove_topic))
        self.app.add_handler(CommandHandler("list_topics", self._cmd_list_topics))
        # Source management
        self.app.add_handler(CommandHandler("sources", self._cmd_sources))
        self.app.add_handler(CommandHandler("enable_home_feed", self._cmd_enable_home_feed))
        self.app.add_handler(CommandHandler("disable_home_feed", self._cmd_disable_home_feed))
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))
        self.app.add_error_handler(self._error_handler)

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
                - id: Queue ID (UUID) for database operations/callbacks
                - target_tweet_id: Twitter's numeric tweet ID for URL
                - author: Tweet author handle
                - content: Tweet text content
            suggested_reply: AI-generated reply suggestion.

        Returns:
            Message ID of the sent message.
        """
        queue_id = tweet_data.get("id", "")
        # Use target_tweet_id for URL (Twitter's numeric ID), fallback to queue_id for backwards compat
        twitter_tweet_id = tweet_data.get("target_tweet_id", queue_id)
        author = tweet_data.get("author", "unknown")
        content = tweet_data.get("content", "")
        
        # Construct tweet URL using the actual Twitter tweet ID
        tweet_url = f"https://x.com/{author}/status/{twitter_tweet_id}"

        message = (
            f"*New Tweet to Reply*\n"
            f"[Link to Tweet]({tweet_url})\n\n"
            f"*@{author}:*\n"
            f"```\n"
            f"\n{content}\n"
            f"```\n\n"
            f"*Suggested Reply:*\n"
            f"```\n"
            f"\n{suggested_reply}\n"
            f"```"
        )

        # Use queue_id (UUID) for callbacks since that's what the database uses
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve:{queue_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit:{queue_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject:{queue_id}"),
            ]
        ])

        msg = await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

        logger.info(f"Sent approval request for queue_id={queue_id}, tweet_id={twitter_tweet_id}")
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
        Send detailed notification that a tweet was published.

        Args:
            tweet: Published tweet data from database, including:
                - id: Queue ID (UUID)
                - target_tweet_id: Twitter's numeric tweet ID
                - target_author: Author of original tweet
                - target_content: Original tweet content
                - reply_text: The reply that was posted
        """
        queue_id = tweet.get("id", "unknown")
        target_tweet_id = tweet.get("target_tweet_id", "")
        author = tweet.get("target_author", "unknown")
        reply_text = tweet.get("reply_text", "")
        
        # Construct the original tweet URL
        tweet_url = f"https://x.com/{author}/status/{target_tweet_id}" if target_tweet_id else "N/A"
        
        # Truncate reply if too long
        reply_preview = reply_text[:200] + "..." if len(reply_text) > 200 else reply_text
        
        message = (
            f"📤 *Reply Published Successfully!*\n\n"
            f"*To:* @{author}\n"
            f"*Original Tweet:* [View]({tweet_url})\n\n"
            f"*Reply Posted:*\n"
            f"```\n{reply_preview}\n```\n\n"
            f"_Queue ID: {queue_id}_"
        )

        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )

    async def send_publication_failure(self, tweet: dict, error: str) -> None:
        """
        Send detailed notification that a tweet publication failed.

        Args:
            tweet: Tweet data from database, including:
                - id: Queue ID (UUID)
                - target_tweet_id: Twitter's numeric tweet ID
                - target_author: Author of original tweet
                - reply_text: The reply that failed to post
            error: Error message describing the failure
        """
        queue_id = tweet.get("id", "unknown")
        target_tweet_id = tweet.get("target_tweet_id", "")
        author = tweet.get("target_author", "unknown")
        reply_text = tweet.get("reply_text", "")
        
        # Construct the original tweet URL
        tweet_url = f"https://x.com/{author}/status/{target_tweet_id}" if target_tweet_id else "N/A"
        
        # Truncate reply and error if too long
        reply_preview = reply_text[:150] + "..." if len(reply_text) > 150 else reply_text
        error_preview = error[:300] + "..." if len(error) > 300 else error
        
        message = (
            f"❌ *Publication Failed*\n\n"
            f"*To:* @{author}\n"
            f"*Original Tweet:* [View]({tweet_url})\n\n"
            f"*Reply (not posted):*\n"
            f"```\n{reply_preview}\n```\n\n"
            f"*Error:*\n`{error_preview}`\n\n"
            f"_Queue ID: {queue_id}_\n"
            f"_Tweet added to Dead Letter Queue for retry_"
        )

        await self.app.bot.send_message(
            chat_id=self.chat_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )


    async def send_startup_notification(self) -> None:
        """Send notification that the bot has started and pin a help message."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Build comprehensive help message with all commands
        help_message = self._build_help_message()
        
        startup_message = (
            f"🚀 *Bot Started*\n\n"
            f"*Time:* {timestamp}\n"
            f"*Account:* @{settings.main_account_handle}\n"
            f"*Mode:* {'Burst' if settings.burst_mode_enabled else 'Normal'}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{help_message}"
        )
        
        try:
            # Send the startup message with commands
            msg = await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=startup_message,
                parse_mode="Markdown",
            )
            logger.info("Sent startup notification with commands")
            
            # Try to pin the message for easy reference
            try:
                await self.app.bot.pin_chat_message(
                    chat_id=self.chat_id,
                    message_id=msg.message_id,
                    disable_notification=True,  # Don't spam everyone
                )
                logger.info("Pinned startup help message")
            except Exception as pin_error:
                # Pinning might fail if bot doesn't have permission
                logger.warning(f"Could not pin startup message: {pin_error}")
                
        except Exception as e:
            logger.error(f"Failed to send startup notification: {e}")

    def _build_help_message(self) -> str:
        """Build a comprehensive help message with all available commands."""
        # Define command categories with their commands
        categories = {
            "📊 Status & Info": [
                {"cmd": "start", "syntax": "/start", "desc": "Show this help message"},
                {"cmd": "queue", "syntax": "/queue", "desc": "Show pending tweets queue"},
                {"cmd": "stats", "syntax": "/stats", "desc": "Show bot statistics"},
                {"cmd": "settings", "syntax": "/settings", "desc": "View/modify bot settings"},
                {"cmd": "sources", "syntax": "/sources", "desc": "Show all tweet sources status"},
            ],
            "🎯 Target Accounts": [
                {"cmd": "add_target", "syntax": "/add_target @user", "desc": "Add accounts to monitor"},
                {"cmd": "remove_target", "syntax": "/remove_target @user", "desc": "Stop monitoring accounts"},
                {"cmd": "list_targets", "syntax": "/list_targets", "desc": "Show monitored accounts"},
            ],
            "🔍 Search Queries": [
                {"cmd": "add_search", "syntax": "/add_search <query>", "desc": "Add a search query"},
                {"cmd": "remove_search", "syntax": "/remove_search <query>", "desc": "Remove a search query"},
                {"cmd": "list_searches", "syntax": "/list_searches", "desc": "List active search queries"},
            ],
            "🏷️ Topic Filters": [
                {"cmd": "add_topic", "syntax": "/add_topic <keyword>", "desc": "Add a topic filter"},
                {"cmd": "remove_topic", "syntax": "/remove_topic <keyword>", "desc": "Remove a topic filter"},
                {"cmd": "list_topics", "syntax": "/list_topics", "desc": "List active topics"},
            ],
            "🏠 Home Feed": [
                {"cmd": "enable_home_feed", "syntax": "/enable_home_feed", "desc": "Enable home feed monitoring"},
                {"cmd": "disable_home_feed", "syntax": "/disable_home_feed", "desc": "Disable home feed monitoring"},
            ],
        }

        lines = ["*📋 Available Commands*\n"]

        for category_name, category_cmds in categories.items():
            lines.append(f"*{category_name}*")
            for item in category_cmds:
                lines.append(f"  `{item['syntax']}` — {item['desc']}")
            lines.append("")

        lines.append("💡 _Use /sources to see all active sources_")
        
        return "\n".join(lines)

    def _escape_markdown(self, text: str) -> str:
        """Escape special Telegram Markdown characters to prevent parsing errors."""
        # Characters that need escaping in Telegram Markdown
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
        for char in special_chars:
            text = text.replace(char, f'\\{char}')
        return text

    async def send_stop_notification(self, reason: str = "Manual shutdown") -> None:
        """
        Send notification that the bot has stopped.

        Args:
            reason: Reason for stopping.
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🛑 *Bot Stopped*\n\n"
            f"*Time:* {timestamp}\n"
            f"*Reason:* {reason}"
        )
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode="Markdown",
            )
            logger.info("Sent stop notification")
        except Exception as e:
            logger.error(f"Failed to send stop notification: {e}")

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
        """Handle /start command - show welcome message with all available commands."""
        
        # 1. Discover all registered commands
        commands = []
        handlers = self.app.handlers.get(0, [])
        
        for handler in handlers:
            if isinstance(handler, CommandHandler):
                for cmd in handler.commands:
                    # Extract description from docstring
                    doc = handler.callback.__doc__ or "No description"
                    desc = doc.split("\n")[0]
                    # Clean up standard docstring format "Handle /cmd - description"
                    if " - " in desc:
                        desc = desc.split(" - ", 1)[1]
                    
                    commands.append({"cmd": cmd, "desc": desc})

        # 2. Categorize commands heuristically
        categories = {
            "📊 Status & Info": [],
            "🎯 Target Accounts": [],
            "🔍 Search Queries": [],
            "🏷️ Topic Filters": [],
            "🏠 Home Feed": [],
            "🛠️ Other Commands": []
        }

        # Define priority order for Status category
        status_priority = ["start", "queue", "stats", "settings", "sources"]

        for cmd_data in commands:
            cmd = cmd_data["cmd"]
            
            if cmd in status_priority:
                categories["📊 Status & Info"].append(cmd_data)
            elif "target" in cmd:
                categories["🎯 Target Accounts"].append(cmd_data)
            elif "search" in cmd:
                categories["🔍 Search Queries"].append(cmd_data)
            elif "topic" in cmd:
                categories["🏷️ Topic Filters"].append(cmd_data)
            elif "home_feed" in cmd:
                categories["🏠 Home Feed"].append(cmd_data)
            else:
                categories["🛠️ Other Commands"].append(cmd_data)

        # Sort commands within categories
        for cat in categories:
            if cat == "📊 Status & Info":
                # Custom sort for status
                categories[cat].sort(key=lambda x: status_priority.index(x["cmd"]) if x["cmd"] in status_priority else 99)
            else:
                categories[cat].sort(key=lambda x: x["cmd"])

        # 3. Build help message
        message = ["👋 *Reply Guy Bot*", "", "Automated Twitter reply assistant with multi-source tweet discovery.", ""]

        for category_name, category_cmds in categories.items():
            if not category_cmds:
                continue

            message.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")
            message.append(f"*{category_name}*")
            message.append(f"━━━━━━━━━━━━━━━━━━━━━━━━━━")

            for item in category_cmds:
                cmd = item["cmd"]
                desc = item["desc"]
                
                # Add syntax hint based on command name
                syntax = f"/{cmd}"
                if cmd.startswith("add_") or cmd.startswith("remove_"):
                    if "target" in cmd: syntax += " @user"
                    elif "search" in cmd: syntax += " <query>"
                    elif "topic" in cmd: syntax += " <keyword>"
                
                message.append(f"`{syntax}` — {desc}")
            
            message.append("")

        message.append("💡 _Use /sources to see all active sources_")

        await update.message.reply_text("\n".join(message), parse_mode="Markdown")

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
                target_tweet_id = tweet.get("target_tweet_id", "")
                reply_preview = tweet.get("reply_text", "")[:50] + "..."
                scheduled_at = tweet.get("scheduled_at", "")
                
                # Format scheduled time
                if scheduled_at:
                    try:
                        # Parse ISO format and display nicely
                        from datetime import datetime
                        if isinstance(scheduled_at, str):
                            dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
                            scheduled_str = dt.strftime("%H:%M:%S")
                        else:
                            scheduled_str = str(scheduled_at)
                    except:
                        scheduled_str = str(scheduled_at)
                else:
                    scheduled_str = "Not scheduled"
                
                # Construct tweet URL
                tweet_url = f"https://x.com/{author}/status/{target_tweet_id}" if target_tweet_id else ""
                tweet_link = f"[Tweet]({tweet_url})" if tweet_url else "N/A"

                lines.append(
                    f"{i}. *@{author}* — {tweet_link}\n"
                    f"   📝 _{reply_preview}_\n"
                    f"   ⏰ {scheduled_str}\n"
                )

            if len(pending) > 10:
                lines.append(f"\n_...and {len(pending) - 10} more_")

            await update.message.reply_text(
                "\n".join(lines),
                parse_mode="Markdown",
                disable_web_page_preview=True,
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
    # Search Query Commands
    # =========================================================================

    async def _cmd_add_search(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /add_search <query> - add a search query."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /add_search <query>\n"
                "Example: /add_search AI startup"
            )
            return

        try:
            query = " ".join(context.args)
            status = await self._db.add_search_query(query)

            if status == "added":
                await update.message.reply_text(f"✅ Added search query: `{query}`", parse_mode="Markdown")
            elif status == "re-enabled":
                await update.message.reply_text(f"🔄 Re-enabled search query: `{query}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"ℹ️ Search query already active: `{query}`", parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error adding search query: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_remove_search(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /remove_search <query> - remove a search query."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text("Usage: /remove_search <query>")
            return

        try:
            query = " ".join(context.args)
            await self._db.remove_search_query(query)
            await update.message.reply_text(f"✅ Removed search query: `{query}`", parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error removing search query: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_list_searches(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /list_searches - list all active search queries."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            searches = await self._db.get_search_queries()
            if not searches:
                await update.message.reply_text("No search queries configured")
                return

            lines = ["🔍 *Active search queries:*"]
            for s in searches:
                lines.append(f"  • `{s['query']}` ({s['product']})")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error listing searches: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    # =========================================================================
    # Topic Commands
    # =========================================================================

    async def _cmd_add_topic(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /add_topic <keyword> - add a topic filter."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text(
                "Usage: /add_topic <keyword>\n"
                "Example: /add_topic machine learning"
            )
            return

        try:
            keyword = " ".join(context.args)
            status = await self._db.add_topic(keyword)

            if status == "added":
                await update.message.reply_text(f"✅ Added topic: `{keyword}`", parse_mode="Markdown")
            elif status == "re-enabled":
                await update.message.reply_text(f"🔄 Re-enabled topic: `{keyword}`", parse_mode="Markdown")
            else:
                await update.message.reply_text(f"ℹ️ Topic already active: `{keyword}`", parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error adding topic: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_remove_topic(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /remove_topic <keyword> - remove a topic filter."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        if not context.args:
            await update.message.reply_text("Usage: /remove_topic <keyword>")
            return

        try:
            keyword = " ".join(context.args)
            await self._db.remove_topic(keyword)
            await update.message.reply_text(f"✅ Removed topic: `{keyword}`", parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error removing topic: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_list_topics(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /list_topics - list all active topics."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            topics = await self._db.get_topics()
            if not topics:
                await update.message.reply_text(
                    "No topic filters configured.\n"
                    "All tweets will be processed."
                )
                return

            lines = ["🏷️ *Active topic filters:*"]
            for t in topics:
                lines.append(f"  • `{t}`")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error listing topics: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    # =========================================================================
    # Source Management Commands
    # =========================================================================

    async def _cmd_sources(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /sources - show all tweet sources and their status."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            lines = ["📡 *Tweet Sources Status*\n"]

            # Target accounts
            targets = await self._db.get_target_accounts()
            lines.append(f"*Target Accounts:* {len(targets)} active")
            if targets:
                for t in targets[:5]:
                    lines.append(f"  • @{t}")
                if len(targets) > 5:
                    lines.append(f"  _...and {len(targets) - 5} more_")

            # Search queries
            searches = await self._db.get_search_queries()
            lines.append(f"\n*Search Queries:* {len(searches)} active")
            if searches:
                for s in searches[:5]:
                    # Escape special markdown chars in query to prevent parsing errors
                    safe_query = self._escape_markdown(s['query'])
                    lines.append(f"  • `{safe_query}`")
                if len(searches) > 5:
                    lines.append(f"  _...and {len(searches) - 5} more_")

            # Home feed
            home_settings = await self._db.get_source_settings("home_feed_following")
            home_status = "✅ Enabled" if home_settings.get("enabled") else "❌ Disabled"
            lines.append(f"\n*Home Feed:* {home_status}")

            # Topics
            topics = await self._db.get_topics()
            lines.append(f"\n*Topic Filters:* {len(topics)} active")
            if not topics:
                lines.append("  _No filters - all tweets processed_")

            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error showing sources: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_enable_home_feed(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /enable_home_feed - enable home feed source."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            await self._db.set_source_enabled("home_feed_following", True)
            await update.message.reply_text(
                "✅ Home feed enabled!\n"
                "The bot will now discover tweets from your timeline."
            )

        except Exception as e:
            logger.error(f"Error enabling home feed: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_disable_home_feed(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /disable_home_feed - disable home feed source."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            await self._db.set_source_enabled("home_feed_following", False)
            await update.message.reply_text("✅ Home feed disabled!")

        except Exception as e:
            logger.error(f"Error disabling home feed: {e}")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_settings(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Handle /settings command - show configurable bot settings."""
        if not self._db:
            await update.message.reply_text("❌ Database not connected")
            return

        try:
            # Get user-specific settings
            user_id = update.effective_user.id
            user_settings = await create_user_settings(user_id, self._db)

            # Build settings menu
            await self._send_settings_menu(user_settings, update.message)

        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            await update.message.reply_text(f"❌ Error loading settings: {e}")

    # =========================================================================
    # Settings Editor UI Methods
    # =========================================================================

    async def _send_settings_menu(
        self,
        user_settings,
        original_message=None
    ) -> None:
        """Send the main settings menu with numbered buttons."""

        # Get settings by category for organized display
        settings_by_category = SettingValidator.get_settings_by_category()
        current_overrides = user_settings.get_all_overrides()

        # Build message with current values
        lines = ["⚙️ *Bot Settings*\n"]

        # Create numbered menu
        setting_number = 1
        setting_map = {}  # number -> setting_key

        for category, category_settings in settings_by_category.items():
            lines.append(f"\n🔸 *{category}*")

            for setting_key, config in category_settings.items():
                # Get current value (user override or default)
                current_value = getattr(user_settings, setting_key)
                default_value = config['default']

                # Format current value for display
                if isinstance(current_value, bool):
                    current_display = '✅ Enabled' if current_value else '❌ Disabled'
                elif isinstance(current_value, (int, float)):
                    unit = 'min' if 'minutes' in config['description'] else ''
                    unit = unit or 'posts' if 'posts' in config['description'] else unit
                    unit = unit or 'hours' if 'hours' in config['description'] else unit
                    current_display = f"{current_value} {unit}".strip()
                else:
                    current_display = str(current_value)

                # Show if user has overridden this setting
                override_indicator = " 🔧" if setting_key in current_overrides else ""

                lines.append(f"{setting_number}. {config['description']}: {current_display}{override_indicator}")

                # Map number to setting key
                setting_map[str(setting_number)] = setting_key
                setting_number += 1

        lines.append("\n*Use /settings_reset to clear all custom settings*")

        # Create numbered keyboard buttons (3 columns)
        keyboard = []
        row = []
        for i in range(1, setting_number):
            row.append(InlineKeyboardButton(f"{i}", callback_data=f"setting_select:{i}"))

            # Create new row every 3 buttons
            if len(row) == 3:
                keyboard.append(row)
                row = []

        # Add remaining buttons
        if row:
            keyboard.append(row)

        # Add action buttons
        keyboard.extend([
            [InlineKeyboardButton("🔄 Reset All", callback_data="setting_reset_all")],
            [InlineKeyboardButton("📊 History", callback_data="setting_history")],
            [InlineKeyboardButton("❌ Cancel", callback_data="setting_cancel")],
        ])

        reply_markup = InlineKeyboardMarkup(keyboard)

        if original_message:
            # Edit existing message
            await original_message.edit_text(
                "\n".join(lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
        else:
            # Send new message (would be stored for editing)
            return await self.app.bot.send_message(
                chat_id=self.chat_id,
                text="\n".join(lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

    async def _send_setting_selection(
        self,
        setting_number: str,
        user_settings,
        original_message
    ) -> None:
        """Send detailed setting selection interface."""

        try:
            # Get setting configuration
            settings_by_category = SettingValidator.get_settings_by_category()

            # Find the setting by number
            setting_key = None
            setting_config = None
            current_number = 1

            for category, category_settings in settings_by_category.items():
                for key, config in category_settings.items():
                    if str(current_number) == setting_number:
                        setting_key = key
                        setting_config = config
                        break
                    current_number += 1
                if setting_key:
                    break

            if not setting_key or not setting_config:
                await original_message.edit_text("❌ Setting not found")
                return

            # Get current and default values
            current_value = getattr(user_settings, setting_key)
            default_value = setting_config['default']
            user_overrides = user_settings.get_all_overrides()

            # Build detailed message
            lines = [
                f"📝 *Edit Setting: {setting_config['description']}*\n",
                f"*Category:* {setting_config['category']}\n",
                f"*Description:* {setting_config['description']}\n",
                f"*Current Value:* {self._format_setting_value(current_value, setting_config)}\n",
                f"*Default Value:* {self._format_setting_value(default_value, setting_config)}\n"
            ]

            # Add range/examples if available
            if 'examples' in setting_config:
                examples_text = ', '.join(setting_config['examples'])
                lines.append(f"*Examples:* {examples_text}\n")

            if 'min' in setting_config and 'max' in setting_config:
                lines.append(f"*Valid Range:* {setting_config['min']} - {setting_config['max']}\n")

            # Show guided input buttons if available
            keyboard = []

            if 'guided_options' in setting_config:
                lines.append(f"*Choose from common values:*\n")

                # Add guided options
                guided_row = []
                for value, label in setting_config['guided_options'][:6]:  # Limit to 6 options
                    guided_row.append(InlineKeyboardButton(
                        label,
                        callback_data=f"setting_set:{setting_key}:{value}"
                    ))
                    if len(guided_row) == 2:
                        keyboard.append(guided_row)
                        guided_row = []

                if guided_row:
                    keyboard.append(guided_row)

                lines.append(f"\n*Or enter a custom value below:*")
            else:
                lines.append(f"\n*Please enter the new value:*\n")

            # Add action buttons
            keyboard.extend([
                [InlineKeyboardButton("🔄 Reset to Default", callback_data=f"setting_reset:{setting_key}")],
                [InlineKeyboardButton("❌ Back to Menu", callback_data="setting_menu")],
            ])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await original_message.edit_text(
                "\n".join(lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

        except Exception as e:
            logger.error(f"Error showing setting selection: {e}")
            await original_message.edit_text(f"❌ Error: {e}")

    async def _send_setting_confirmation(
        self,
        setting_key: str,
        new_value: Any,
        user_settings,
        original_message
    ) -> None:
        """Send confirmation dialog for setting change."""

        try:
            # Get setting configuration
            setting_config = SettingValidator.get_setting_info(setting_key)
            current_value = getattr(user_settings, setting_key)

            # Format values for display
            current_display = self._format_setting_value(current_value, setting_config)
            new_display = self._format_setting_value(new_value, setting_config)

            # Build confirmation message
            lines = [
                f"📝 *Confirm Setting Change*\n\n",
                f"*Setting:* {setting_config['description']}\n",
                f"*Current:* {current_display}\n",
                f"*New:* {new_display}\n\n",
                f"*Impact:* {self._get_setting_impact(setting_key, current_value, new_value)}\n\n",
                "This change will take effect immediately for future operations.\n\n",
                "Confirm this change?"
            ]

            keyboard = [
                [
                    InlineKeyboardButton("✅ Confirm", callback_data=f"setting_confirm:{setting_key}:{new_value}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"setting_select_menu")
                ]
            ]

            reply_markup = InlineKeyboardMarkup(keyboard)

            await original_message.edit_text(
                "\n".join(lines),
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )

        except Exception as e:
            logger.error(f"Error showing confirmation: {e}")
            await original_message.edit_text(f"❌ Error: {e}")

    def _format_setting_value(self, value: Any, config: dict) -> str:
        """Format a setting value for display."""
        if isinstance(value, bool):
            return '✅ Enabled' if value else '❌ Disabled'
        elif isinstance(value, (int, float)):
            # Add appropriate units
            if 'minutes' in config.get('description', ''):
                return f"{value} minutes"
            elif 'hours' in config.get('description', ''):
                return f"{value} hours"
            elif 'posts' in config.get('description', ''):
                return f"{value} posts"
            else:
                return str(value)
        elif isinstance(value, str):
            # Check if it's an AI model and show friendly name
            if value in SettingValidator.AI_MODEL_CHOICES:
                return SettingValidator.AI_MODEL_CHOICES[value]
            return value
        else:
            return str(value)

    def _get_setting_impact(self, setting_key: str, old_value: Any, new_value: Any) -> str:
        """Get user-friendly description of setting change impact."""
        impacts = {
            'burst_mode_enabled': lambda o, n: "Burst mode will be " + ("enabled" if n else "disabled"),
            'quiet_hours_start': lambda o, n: f"Quiet hours will start at {n:02d}:00 instead of {o:02d}:00",
            'quiet_hours_end': lambda o, n: f"Quiet hours will end at {n:02d}:00 instead of {o:02d}:00",
            'min_delay_minutes': lambda o, n: f"Minimum delay will be {n} minutes instead of {o} minutes",
            'max_delay_minutes': lambda o, n: f"Maximum delay will be {n} minutes instead of {o} minutes",
            'max_posts_per_hour': lambda o, n: f"Maximum posts per hour will be {n} instead of {o}",
            'max_posts_per_day': lambda o, n: f"Maximum posts per day will be {n} instead of {o}",
            'ghost_delegate_enabled': lambda o, n: "Ghost delegate will be " + ("enabled" if n else "disabled"),
            'login_cooldown_hours': lambda o, n: f"Login cooldown will be {n} hours instead of {o} hours",
            'ai_model': lambda o, n: f"AI model will change to {SettingValidator.AI_MODEL_CHOICES.get(n, n)}",
            'ai_base_url': lambda o, n: f"API base URL will change to {n}",
        }

        impact_func = impacts.get(setting_key)
        return impact_func(old_value, new_value) if impact_func else f"Setting will change from {old_value} to {new_value}"

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
        
        try:
            await query.answer()
        except BadRequest as e:
            if "Query is too old" in str(e):
                # Ignore this error to allow processing the action (e.g. approve/reject)
                # even if the button is old
                logger.warning(f"Callback query too old (ignored): {e}")
            else:
                # Log other bad request errors but don't crash
                logger.error(f"Bad request in callback answer: {e}")
        except Exception as e:
            logger.error(f"Error answering callback query: {e}")


        action, *parts = query.data.split(":")

        # Handle tweet approval callbacks
        if action == "approve" and self._on_approve:
            tweet_id = parts[0] if parts else ""
            await self._on_approve(tweet_id)
            await query.edit_message_reply_markup(reply_markup=None)

        elif action == "reject" and self._on_reject:
            tweet_id = parts[0] if parts else ""
            await self._on_reject(tweet_id)
            await query.edit_message_text("❌ Rejected")

        elif action == "edit":
            # Edit interface deferred for MVP
            await query.message.reply_text(
                "✏️ Edit feature coming soon!\n\n"
                "For now, please reject and wait for a new suggestion."
            )

        # Handle settings editor callbacks
        elif action == "setting_menu":
            await self._handle_settings_menu(query)

        elif action == "setting_select":
            setting_number = parts[0] if parts else ""
            await self._handle_setting_selection(query, setting_number)

        elif action == "setting_set":
            setting_key, new_value = parts[0], parts[1] if len(parts) >= 2 else (parts[0], "")
            await self._handle_setting_set(query, setting_key, new_value)

        elif action == "setting_confirm":
            setting_key, new_value = parts[0], parts[1] if len(parts) >= 2 else (parts[0], "")
            await self._handle_setting_confirm(query, setting_key, new_value)

        elif action == "setting_reset":
            setting_key = parts[0] if parts else ""
            await self._handle_setting_reset(query, setting_key)

        elif action == "setting_reset_all":
            await self._handle_setting_reset_all(query)

        elif action == "setting_history":
            await self._handle_setting_history(query)

        elif action == "setting_cancel":
            await query.edit_message_text("Settings editor cancelled.")

        elif action == "setting_select_menu":
            await self._handle_settings_menu(query)

        else:
            logger.warning(f"Unknown callback action: {action}")
            await query.edit_message_text("❌ Unknown action")

    # =========================================================================
    # Settings Callback Handlers
    # =========================================================================

    async def _handle_settings_menu(self, query) -> None:
        """Handle settings menu display."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            await self._send_settings_menu(user_settings, query.message)

        except Exception as e:
            logger.error(f"Error in settings menu: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_selection(self, query, setting_number: str) -> None:
        """Handle setting selection from main menu."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            await self._send_setting_selection(setting_number, user_settings, query.message)

        except Exception as e:
            logger.error(f"Error in setting selection: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_set(self, query, setting_key: str, new_value: str) -> None:
        """Handle guided setting selection."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            # Convert string value to appropriate type based on setting config
            config = SettingValidator.get_setting_info(setting_key)
            expected_type = config['type']

            if expected_type == bool:
                validated_value = new_value.lower() in ('true', '1', 'yes', 'on', 'enabled')
            elif expected_type == int:
                try:
                    validated_value = int(new_value)
                except ValueError:
                    await query.edit_message_text(f"❌ Invalid number: {new_value}")
                    return
            elif expected_type == float:
                try:
                    validated_value = float(new_value)
                except ValueError:
                    await query.edit_message_text(f"❌ Invalid decimal: {new_value}")
                    return
            else:
                validated_value = new_value

            await self._send_setting_confirmation(setting_key, validated_value, user_settings, query.message)

        except Exception as e:
            logger.error(f"Error in setting set: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_confirm(self, query, setting_key: str, new_value: str) -> None:
        """Handle setting change confirmation."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            # Convert string value to appropriate type
            config = SettingValidator.get_setting_info(setting_key)
            expected_type = config['type']

            if expected_type == bool:
                validated_value = new_value.lower() in ('true', '1', 'yes', 'on', 'enabled')
            elif expected_type == int:
                validated_value = int(new_value)
            elif expected_type == float:
                validated_value = float(new_value)
            else:
                validated_value = new_value

            # Update setting in database
            success = await self._db.update_user_settings(
                telegram_user_id=user_id,
                settings_overrides={setting_key: validated_value},
                change_reason=f"Changed via Telegram settings editor"
            )

            if success:
                # Refresh user settings with new overrides
                updated_user_settings = await create_user_settings(user_id, self._db)

                await query.edit_message_text(
                    f"✅ *Setting Updated*\n\n"
                    f"*{config['description']}*\n"
                    f"New value: {self._format_setting_value(validated_value, config)}\n\n"
                    "Changes take effect immediately."
                )

                # Refresh settings menu after 2 seconds
                import asyncio
                await asyncio.sleep(2)
                await self._send_settings_menu(updated_user_settings, query.message)

            else:
                await query.edit_message_text("❌ Failed to update setting in database")

        except Exception as e:
            logger.error(f"Error confirming setting: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_reset(self, query, setting_key: str) -> None:
        """Handle individual setting reset."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            # Check if setting is actually overridden
            if not user_settings.has_user_override(setting_key):
                await query.edit_message_text("ℹ️ This setting is already at default value")
                return

            # Remove the override
            user_settings.remove_override(setting_key)

            # Update database to remove override
            success = await self._db.update_user_settings(
                telegram_user_id=user_id,
                settings_overrides=user_settings.get_all_overrides(),
                change_reason=f"Reset {setting_key} to default"
            )

            if success:
                config = SettingValidator.get_setting_info(setting_key)
                await query.edit_message_text(
                    f"✅ *Setting Reset*\n\n"
                    f"*{config['description']}*\n"
                    f"Reset to default: {self._format_setting_value(config['default'], config)}\n\n"
                    "Changes take effect immediately."
                )

                # Refresh settings menu after 2 seconds
                import asyncio
                await asyncio.sleep(2)
                await self._send_settings_menu(user_settings, query.message)

            else:
                await query.edit_message_text("❌ Failed to reset setting")

        except Exception as e:
            logger.error(f"Error resetting setting: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_reset_all(self, query) -> None:
        """Handle reset all settings."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id
            user_settings = await create_user_settings(user_id, self._db)

            current_overrides = user_settings.get_all_overrides()
            if not current_overrides:
                await query.edit_message_text("ℹ️ No custom settings to reset")
                return

            # Reset all overrides
            success = await self._db.reset_user_settings(
                telegram_user_id=user_id,
                change_reason="Reset all settings to defaults"
            )

            if success:
                await query.edit_message_text(
                    f"✅ *All Settings Reset*\n\n"
                    f"Reset {len(current_overrides)} settings to defaults\n\n"
                    "Changes take effect immediately."
                )

                # Refresh settings menu after 2 seconds
                import asyncio
                await asyncio.sleep(2)
                reset_user_settings = await create_user_settings(user_id, self._db)
                await self._send_settings_menu(reset_user_settings, query.message)

            else:
                await query.edit_message_text("❌ Failed to reset settings")

        except Exception as e:
            logger.error(f"Error resetting all settings: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    async def _handle_setting_history(self, query) -> None:
        """Handle settings history display."""
        try:
            # Get user-specific settings
            user_id = query.from_user.id

            # Get settings history
            history = await self._db.get_settings_history(user_id, limit=10)

            if not history:
                await query.edit_message_text("📜 *Settings History*\n\nNo changes recorded yet")
                return

            # Format history
            lines = ["📜 *Recent Settings Changes*\n"]

            for change in history:
                setting_key = change['setting_key']
                old_value = change['old_value']
                new_value = change['new_value']
                changed_at = change['changed_at']

                # Get setting info for friendly name
                try:
                    config = SettingValidator.get_setting_info(setting_key)
                    setting_name = config['description']
                except:
                    setting_name = setting_key

                # Format values
                old_display = self._format_simple_value(old_value)
                new_display = self._format_simple_value(new_value)

                lines.append(
                    f"*{setting_name}*\n"
                    f"  {old_display} → {new_display}\n"
                    f"  _{changed_at}_"
                )

            lines.append("\n*Back to Menu:* /settings")

            await query.edit_message_text(
                "\n".join(lines),
                parse_mode="Markdown"
            )

        except Exception as e:
            logger.error(f"Error showing history: {e}")
            await query.edit_message_text(f"❌ Error: {e}")

    def _format_simple_value(self, value) -> str:
        """Format value for history display."""
        if value is None:
            return "Default"
        elif isinstance(value, bool):
            return '✅' if value else '❌'
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            # Show AI model friendly name
            return SettingValidator.AI_MODEL_CHOICES.get(value, value)
        else:
            return str(value)

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

    # =========================================================================
    # Settings Editor Helper Methods
    # =========================================================================

    async def get_user_settings_display(self, user_id: int) -> str:
        """Get formatted display of current user settings."""
        try:
            user_settings = await create_user_settings(user_id, self._db)
            overrides = user_settings.get_all_overrides()

            if not overrides:
                return "ℹ️ No custom settings configured\n\nUse /settings to configure"

            lines = ["🔧 *User Settings*\n"]
            for key, value in overrides.items():
                try:
                    config = SettingValidator.get_setting_info(key)
                    formatted_value = self._format_setting_value(value, config)
                    lines.append(f"  • {config['description']}: {formatted_value}")
                except Exception:
                    lines.append(f"  • {key}: {value}")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"Error getting user settings display: {e}")
            return f"❌ Error: {e}"

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and send a telegram message to notify the developer."""
        logger.error(
            msg="Exception while handling an update:",
            exc_info=context.error
        )

