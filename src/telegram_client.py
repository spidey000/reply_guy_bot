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
            "/settings - ⚙️ Configure bot settings\n"
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
        await query.answer()

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
