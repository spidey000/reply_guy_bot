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
    # AI Provider (OpenAI API Compatible)
    # =========================================================================
    ai_api_key: str
    ai_base_url: str = "https://api.openai.com/v1"
    ai_model: str = "gpt-4o-mini"

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


# Singleton instance
settings = Settings()
