"""
Centralized configuration for Reply Guy Bot.

This module uses Pydantic Settings to load and validate environment variables.
All bot configuration is centralized here to avoid scattered config files.

Usage:
    from config import settings
    print(settings.ai_model)

Environment Variables:
    See .env.example for all available configuration options.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    ai_model: str = "openai/gpt-4o-mini"
    # Model options (change ai_model above):
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
    # Cookie Encryption (Security)
    # =========================================================================
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    cookie_encryption_key: str = ""  # Required for cookie encryption


# Singleton instance
settings = Settings()
