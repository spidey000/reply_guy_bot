"""
Centralized configuration for Reply Guy Bot.

This module uses Pydantic Settings to load and validate environment variables.
All bot configuration is centralized here to avoid scattered config files.

Usage:
    from config import settings
    print(settings.ai_model)

    # Load with user-specific overrides (for Telegram settings editor)
    from config import create_user_settings
    user_settings = await create_user_settings(telegram_user_id=123456789)

Environment Variables:
    See .env.example for all available configuration options.
"""

from typing import Dict, Any, Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
import json
import logging

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================================================================
    # X/Twitter Ghost Delegate
    # =========================================================================
    dummy_username: str
    dummy_email: str
    dummy_password: str
    main_account_handle: str

    # =========================================================================
    # AI Provider (OpenRouter)
    # =========================================================================
    # Get your API key from https://openrouter.ai/keys
    ai_api_key: str
    ai_base_url: str = "https://openrouter.ai/api/v1"
    ai_model: str = "x-ai/grok-4.1-fast:free"
    # Model options (change ai_model above):
    # "x-ai/grok-4.1-fast:free"    - Grok 4.1 Fast (free)
    # "openai/gpt-4o-mini"          - OpenAI GPT-4o Mini (cheap & fast)
    # "deepseek/deepseek-chat"      - DeepSeek V3 (very cheap)
    # "google/gemini-flash-1.5"     - Gemini Flash (fast)
    # "google/gemini-2.0-flash-001" - Gemini 2.0 Flash (latest)

    # =========================================================================
    # Telegram
    # =========================================================================
    telegram_bot_token: str
    telegram_chat_id: str

    # =========================================================================
    # Supabase
    # =========================================================================
    supabase_url: str
    supabase_key: str

    # =========================================================================
    # Burst Mode (Anti-Detection)
    # =========================================================================
    burst_mode_enabled: bool = True
    quiet_hours_start: int = 0  # 00:00
    quiet_hours_end: int = 7  # 07:00
    min_delay_minutes: int = 15
    max_delay_minutes: int = 120
    scheduler_check_interval: int = 60  # seconds

    # =========================================================================
    # Ghost Delegate Security
    # =========================================================================
    ghost_delegate_enabled: bool = True
    ghost_delegate_switch_timeout: int = 30  # seconds

    # =========================================================================
    # Rate Limiting
    # =========================================================================
    max_posts_per_hour: int = 15
    max_posts_per_day: int = 50
    rate_limit_warning_threshold: float = 0.8  # Warn at 80%

    # =========================================================================
    # Login Cooldown (Ban Prevention)
    # =========================================================================
    login_cooldown_hours: int = 3  # Minimum hours between fresh logins
    login_cooldown_enabled: bool = True  # Enable/disable cooldown enforcement

    # =========================================================================
    # Cookie Encryption (Security) - REQUIRED FOR PRODUCTION
    # =========================================================================
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # WARNING: This is MANDATORY in production to protect session cookies.
    cookie_encryption_key: str  # Required - no default, must be set in .env

    # =========================================================================
    # Runtime properties (not from environment)
    # =========================================================================
    _user_overrides: Dict[str, Any] = {}
    _telegram_user_id: Optional[int] = None


class UserSettings(Settings):
    """Extended Settings class with user-specific overrides for Telegram settings editor."""

    def __init__(self, telegram_user_id: Optional[int] = None, user_overrides: Optional[Dict[str, Any]] = None, **kwargs):
        """Initialize settings with optional user-specific overrides.

        Args:
            telegram_user_id: Telegram user ID for settings attribution
            user_overrides: Dictionary of user setting overrides
            **kwargs: Additional settings (typically from environment)
        """
        super().__init__(**kwargs)
        self._telegram_user_id = telegram_user_id
        self._user_overrides = user_overrides or {}

        # Validate user overrides on initialization
        self._validate_user_overrides()

    def _validate_user_overrides(self) -> None:
        """Validate all user overrides against the validation framework."""
        for key, value in self._user_overrides.items():
            try:
                SettingValidator.validate_setting(key, value)
                logger.debug(f"Validated user override: {key}={value}")
            except ValueError as e:
                logger.warning(f"Invalid user override {key}={value}: {e}")
                # Remove invalid override
                del self._user_overrides[key]

    def get_user_value(self, key: str) -> Any:
        """Get user override value if exists, otherwise return default."""
        return self._user_overrides.get(key)

    def has_user_override(self, key: str) -> bool:
        """Check if user has overridden a specific setting."""
        return key in self._user_overrides

    def get_all_overrides(self) -> Dict[str, Any]:
        """Get all user overrides as a dictionary."""
        return self._user_overrides.copy()

    def update_override(self, key: str, value: Any) -> None:
        """Update a single user override with validation."""
        validated_value = SettingValidator.validate_setting(key, value)
        self._user_overrides[key] = validated_value
        logger.info(f"Updated user override: {key}={validated_value}")

    def remove_override(self, key: str) -> bool:
        """Remove a user override, return True if removed."""
        if key in self._user_overrides:
            del self._user_overrides[key]
            logger.info(f"Removed user override: {key}")
            return True
        return False

    def reset_all_overrides(self) -> None:
        """Remove all user overrides."""
        self._user_overrides.clear()
        logger.info("Reset all user overrides")

    def __getattr__(self, name: str) -> Any:
        """Override attribute access to return user overrides when available."""
        # Check if this is a user override first
        if name in self._user_overrides:
            return self._user_overrides[name]

        # Fall back to default behavior
        return super().__getattribute__(name)

    def get_effective_value(self, key: str) -> Any:
        """Get the effective value (user override or default)."""
        if key in self._user_overrides:
            return self._user_overrides[key]
        return getattr(self, key)


class SettingValidator:
    """Validation framework for all editable settings with guided input support."""

    # Available AI models for guided selection
    AI_MODEL_CHOICES = {
        "x-ai/grok-4.1-fast:free": "Grok 4.1 Fast (Free - X AI)",
        "openai/gpt-4o-mini": "GPT-4o Mini (OpenAI - Fast & Cheap)",
        "deepseek/deepseek-chat": "DeepSeek V3 (Very Cheap)",
        "google/gemini-flash-1.5": "Gemini Flash 1.5 (Google - Fast)",
        "google/gemini-2.0-flash-001": "Gemini 2.0 Flash (Google - Latest)"
    }

    SETTINGS_CONFIG = {
        # Burst Mode (Anti-Detection)
        'burst_mode_enabled': {
            'type': bool,
            'category': 'Burst Mode',
            'description': 'Enable burst mode for anti-detection (adds random delays)',
            'default': True,
            'guided_options': [
                ('true', '✅ Enable burst mode'),
                ('false', '❌ Disable burst mode')
            ]
        },
        'quiet_hours_start': {
            'type': int,
            'min': 0,
            'max': 23,
            'category': 'Burst Mode',
            'description': 'Start hour for quiet period (24-hour format)',
            'default': 0,
            'examples': ['0 (midnight)', '6 (6 AM)', '18 (6 PM)', '23 (11 PM)'],
            'guided_options': [(str(i), f'{i:02d}:00') for i in range(24)]
        },
        'quiet_hours_end': {
            'type': int,
            'min': 0,
            'max': 23,
            'category': 'Burst Mode',
            'description': 'End hour for quiet period (24-hour format)',
            'default': 7,
            'examples': ['0 (midnight)', '6 (6 AM)', '18 (6 PM)', '23 (11 PM)'],
            'guided_options': [(str(i), f'{i:02d}:00') for i in range(24)]
        },
        'min_delay_minutes': {
            'type': int,
            'min': 1,
            'max': 1440,  # 24 hours
            'category': 'Burst Mode',
            'description': 'Minimum delay between posts in minutes',
            'default': 15,
            'examples': ['5 (5 minutes)', '30 (30 minutes)', '60 (1 hour)', '240 (4 hours)'],
            'guided_options': [(str(i), f'{i} min') for i in [1, 5, 10, 15, 30, 60, 120, 240, 480, 720, 1440]]
        },
        'max_delay_minutes': {
            'type': int,
            'min': 1,
            'max': 1440,  # 24 hours
            'category': 'Burst Mode',
            'description': 'Maximum delay between posts in minutes',
            'default': 120,
            'examples': ['30 (30 minutes)', '60 (1 hour)', '120 (2 hours)', '480 (8 hours)'],
            'guided_options': [(str(i), f'{i} min') for i in [5, 15, 30, 60, 120, 240, 480, 720, 1440]]
        },

        # AI Configuration
        'ai_model': {
            'type': str,
            'category': 'AI Configuration',
            'description': 'AI model for generating replies',
            'default': 'x-ai/grok-4.1-fast:free',
            'choices': list(AI_MODEL_CHOICES.keys()),
            'guided_options': list(AI_MODEL_CHOICES.items())
        },
        'ai_base_url': {
            'type': str,
            'category': 'AI Configuration',
            'description': 'Base URL for AI API',
            'default': 'https://openrouter.ai/api/v1',
            'examples': ['https://openrouter.ai/api/v1', 'https://api.openai.com/v1'],
            'guided_options': [
                ('https://openrouter.ai/api/v1', 'OpenRouter'),
                ('https://api.openai.com/v1', 'OpenAI'),
                ('custom', 'Custom URL...')
            ]
        },

        # Rate Limiting
        'max_posts_per_hour': {
            'type': int,
            'min': 1,
            'max': 100,
            'category': 'Rate Limiting',
            'description': 'Maximum posts per hour',
            'default': 15,
            'examples': ['5 (conservative)', '15 (balanced)', '30 (aggressive)', '50 (maximum)'],
            'guided_options': [(str(i), f'{i} posts') for i in [1, 5, 10, 15, 20, 30, 50, 75, 100]]
        },
        'max_posts_per_day': {
            'type': int,
            'min': 1,
            'max': 1000,
            'category': 'Rate Limiting',
            'description': 'Maximum posts per day',
            'default': 50,
            'examples': ['10 (minimal)', '50 (balanced)', '100 (active)', '200 (maximum)'],
            'guided_options': [(str(i), f'{i} posts') for i in [5, 10, 25, 50, 75, 100, 150, 200, 500, 1000]]
        },
        'rate_limit_warning_threshold': {
            'type': float,
            'min': 0.1,
            'max': 1.0,
            'category': 'Rate Limiting',
            'description': 'Warning threshold for rate limiting (0.1-1.0)',
            'default': 0.8,
            'examples': ['0.5 (50% - early warning)', '0.8 (80% - balanced)', '0.9 (90% - late warning)'],
            'guided_options': [
                ('0.5', '50% - Early Warning'),
                ('0.7', '70% - Moderate Warning'),
                ('0.8', '80% - Balanced'),
                ('0.9', '90% - Late Warning'),
                ('1.0', '100% - Only at Limit')
            ]
        },

        # Security Settings
        'ghost_delegate_enabled': {
            'type': bool,
            'category': 'Security Settings',
            'description': 'Enable ghost delegate for enhanced security',
            'default': True,
            'guided_options': [
                ('true', '✅ Enable ghost delegate'),
                ('false', '❌ Disable ghost delegate')
            ]
        },
        'login_cooldown_hours': {
            'type': int,
            'min': 1,
            'max': 168,  # 1 week
            'category': 'Security Settings',
            'description': 'Minimum hours between fresh logins',
            'default': 3,
            'examples': ['1 (1 hour)', '6 (6 hours)', '24 (1 day)', '72 (3 days)', '168 (1 week)'],
            'guided_options': [(str(i), f'{i} hours') for i in [1, 2, 3, 6, 12, 24, 48, 72, 168]]
        }
    }

    @classmethod
    def get_all_settings(cls) -> Dict[str, Dict[str, Any]]:
        """Get all available settings with their configurations."""
        return cls.SETTINGS_CONFIG.copy()

    @classmethod
    def get_settings_by_category(cls) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Group settings by category for menu organization."""
        categorized = {}
        for key, config in cls.SETTINGS_CONFIG.items():
            category = config['category']
            if category not in categorized:
                categorized[category] = {}
            categorized[category][key] = config
        return categorized

    @classmethod
    def validate_setting(cls, key: str, value: Any) -> Any:
        """Validate a setting value with comprehensive error messages."""
        if key not in cls.SETTINGS_CONFIG:
            raise ValueError(f"Unknown setting: {key}")

        config = cls.SETTINGS_CONFIG[key]
        expected_type = config['type']

        # Type validation and conversion
        try:
            if expected_type == bool:
                if isinstance(value, str):
                    value = value.lower() in ('true', '1', 'yes', 'on', 'enabled', '✅')
                else:
                    value = bool(value)
            elif expected_type == int:
                value = int(value)
            elif expected_type == float:
                value = float(value)
            elif expected_type == str:
                value = str(value)
        except (ValueError, TypeError):
            type_name = expected_type.__name__
            raise ValueError(f"Must be a {type_name}")

        # Range validation
        if 'min' in config and value < config['min']:
            raise ValueError(f"Minimum value is {config['min']}")
        if 'max' in config and value > config['max']:
            raise ValueError(f"Maximum value is {config['max']}")

        # Choices validation
        if 'choices' in config and value not in config['choices']:
            valid_choices = ', '.join(config['choices'])
            raise ValueError(f"Must be one of: {valid_choices}")

        return value

    @classmethod
    def get_setting_info(cls, key: str) -> Dict[str, Any]:
        """Get detailed information about a setting for display purposes."""
        if key not in cls.SETTINGS_CONFIG:
            raise ValueError(f"Unknown setting: {key}")

        config = cls.SETTINGS_CONFIG[key].copy()

        # Add formatted default value
        default = config['default']
        if isinstance(default, bool):
            config['formatted_default'] = '✅ Enabled' if default else '❌ Disabled'
        elif isinstance(default, (int, float)):
            config['formatted_default'] = str(default)
        else:
            config['formatted_default'] = str(default)

        # Add field name (from key)
        config['field_name'] = key

        return config


# Singleton instance for global settings
settings = Settings()


async def create_user_settings(telegram_user_id: int, database=None) -> UserSettings:
    """Create UserSettings instance with overrides loaded from database.

    Args:
        telegram_user_id: Telegram user ID
        database: Database instance (optional, for loading overrides)

    Returns:
        UserSettings instance with user overrides applied
    """
    user_overrides = {}

    # Load user overrides from database if database is provided
    if database:
        try:
            user_overrides = await database.get_user_settings(telegram_user_id)
        except Exception as e:
            logger.warning(f"Failed to load user settings for {telegram_user_id}: {e}")

    # Create UserSettings instance with global defaults plus user overrides
    global_settings_dict = {
        key: getattr(settings, key)
        for key in SettingValidator.SETTINGS_CONFIG.keys()
        if hasattr(settings, key)
    }

    return UserSettings(
        telegram_user_id=telegram_user_id,
        user_overrides=user_overrides,
        **global_settings_dict
    )
