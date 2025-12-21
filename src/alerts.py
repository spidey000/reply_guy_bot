"""
Centralized Alert Management System

This module provides a centralized way to handle all notifications and alerts
across the bot, with configurable severity levels and multiple dispatch targets.

Features:
- Severity-based filtering (DEBUG, INFO, WARNING, ERROR, CRITICAL)
- Multiple notification channels (logging, Telegram, future: screenshots, etc.)
- Configurable minimum alert level for each channel
- Consistent formatting and categorization

Usage:
    from src.alerts import AlertManager, AlertLevel
    
    alerts = AlertManager(telegram_client=telegram, settings=settings)
    
    # Send alerts with different severity levels
    await alerts.notify(AlertLevel.INFO, "bot_started", "Bot started successfully")
    await alerts.notify(AlertLevel.ERROR, "ai_failure", "AI service unavailable", {"model": "gpt-4"})
    await alerts.notify(AlertLevel.CRITICAL, "auth_failed", "Authentication failed", {"attempts": 3})
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.telegram_client import TelegramClient
    from config.settings import Settings

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Alert severity levels."""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3
    CRITICAL = 4


class AlertManager:
    """
    Centralized alert management with severity-based filtering.
    
    This class handles all notifications and alerts across the bot,
    dispatching them to appropriate channels based on severity level
    and configuration.
    """

    def __init__(
        self,
        telegram_client: Optional["TelegramClient"] = None,
        settings: Optional["Settings"] = None,
    ):
        """
        Initialize the AlertManager.

        Args:
            telegram_client: TelegramClient instance for notifications
            settings: Settings instance for configuration
        """
        self.telegram = telegram_client
        self.settings = settings
        
        # Default minimum alert level for Telegram notifications
        self._min_telegram_level = AlertLevel.WARNING
        
        # Load from settings if available
        if settings:
            self._load_settings()

    def _load_settings(self) -> None:
        """Load alert configuration from settings."""
        if hasattr(self.settings, 'min_telegram_alert_level'):
            try:
                level_str = self.settings.min_telegram_alert_level.upper()
                self._min_telegram_level = AlertLevel[level_str]
                logger.debug(f"Loaded min_telegram_alert_level: {level_str}")
            except (KeyError, AttributeError) as e:
                logger.warning(f"Invalid min_telegram_alert_level, using default WARNING: {e}")

    async def notify(
        self,
        level: AlertLevel,
        alert_type: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Send a notification with the specified severity level.

        The notification is dispatched to:
        1. Logger (always)
        2. Telegram (if level >= min_telegram_level)
        3. Future channels (screenshots, file logging, etc.)

        Args:
            level: Alert severity level
            alert_type: Type/category of alert (e.g., "bot_started", "ai_failure")
            message: Human-readable alert message
            details: Optional dictionary with additional alert details
        """
        # 1. Always log the alert
        self._log_alert(level, alert_type, message, details)

        # 2. Send to Telegram if severity is high enough
        if self.telegram and level.value >= self._min_telegram_level.value:
            await self._send_telegram_alert(level, alert_type, message, details)

        # 3. Future dispatch targets (screenshots, file logging, webhooks, etc.)
        # This is where you can add additional notification mechanisms

    def _log_alert(
        self,
        level: AlertLevel,
        alert_type: str,
        message: str,
        details: Optional[dict[str, Any]],
    ) -> None:
        """Log the alert to the standard logger."""
        log_msg = f"[{alert_type}] {message}"
        if details:
            log_msg += f" | {details}"

        # Map AlertLevel to Python logging levels
        if level == AlertLevel.DEBUG:
            logger.debug(log_msg)
        elif level == AlertLevel.INFO:
            logger.info(log_msg)
        elif level == AlertLevel.WARNING:
            logger.warning(log_msg)
        elif level == AlertLevel.ERROR:
            logger.error(log_msg)
        elif level == AlertLevel.CRITICAL:
            logger.critical(log_msg)

    async def _send_telegram_alert(
        self,
        level: AlertLevel,
        alert_type: str,
        message: str,
        details: Optional[dict[str, Any]],
    ) -> None:
        """Send alert to Telegram."""
        try:
            await self.telegram.send_error_alert(
                error_type=alert_type,
                message=message,
                details=details,
            )
        except Exception as e:
            # Don't let Telegram failures cascade
            logger.error(f"Failed to send Telegram alert: {e}")

    # Convenience methods for common alert types
    
    async def startup(self, **details) -> None:
        """Send a startup notification."""
        if self.telegram:
            await self.telegram.send_startup_notification()
        await self.notify(
            AlertLevel.INFO,
            "bot_started",
            "Bot started successfully",
            details,
        )

    async def shutdown(self, reason: str = "Manual shutdown", **details) -> None:
        """Send a shutdown notification."""
        if self.telegram:
            await self.telegram.send_stop_notification(reason)
        await self.notify(
            AlertLevel.INFO,
            "bot_stopped",
            f"Bot stopped: {reason}",
            details,
        )

    async def error(self, error_type: str, message: str, **details) -> None:
        """Send an error alert."""
        await self.notify(AlertLevel.ERROR, error_type, message, details)

    async def critical(self, error_type: str, message: str, **details) -> None:
        """Send a critical alert."""
        await self.notify(AlertLevel.CRITICAL, error_type, message, details)

    async def warning(self, alert_type: str, message: str, **details) -> None:
        """Send a warning alert."""
        await self.notify(AlertLevel.WARNING, alert_type, message, details)

    async def info(self, alert_type: str, message: str, **details) -> None:
        """Send an info alert."""
        await self.notify(AlertLevel.INFO, alert_type, message, details)


# Global alert manager instance (initialized in bot.py)
_alert_manager: Optional[AlertManager] = None


def initialize_alerts(
    telegram_client: Optional["TelegramClient"] = None,
    settings: Optional["Settings"] = None,
) -> AlertManager:
    """
    Initialize the global AlertManager instance.

    Args:
        telegram_client: TelegramClient instance
        settings: Settings instance

    Returns:
        Initialized AlertManager instance
    """
    global _alert_manager
    _alert_manager = AlertManager(telegram_client=telegram_client, settings=settings)
    logger.info("AlertManager initialized")
    return _alert_manager


def get_alerts() -> Optional[AlertManager]:
    """Get the global AlertManager instance."""
    return _alert_manager
